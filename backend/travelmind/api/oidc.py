from math import isfinite
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def https_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.fragment
        or "\\" in value
        or any(char.isspace() for char in value)
    ):
        raise ValueError("expected an HTTPS URL")
    return value


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.oidc",
        env_file_encoding="utf-8",
        env_prefix="OIDC_",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )

    issuer: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    public_origin: str | None = None
    token_auth_method: str = "client_secret_basic"
    session_seconds: int = Field(default=28_800, ge=60, le=604_800)

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value: str | None) -> str | None:
        if value:
            parsed = urlsplit(https_url(value))
            if parsed.path not in ("", "/") or parsed.query:
                raise ValueError("OIDC_PUBLIC_ORIGIN must be an HTTPS origin")
            return f"{parsed.scheme}://{parsed.netloc}"
        return None

    @model_validator(mode="after")
    def validate_client(self):
        if self.issuer:
            if urlsplit(https_url(self.issuer)).query:
                raise ValueError("OIDC issuer must not have a query string")
            if not self.client_id or not self.public_origin:
                raise ValueError("OIDC issuer requires a client ID and public origin")
            if self.token_auth_method not in {"client_secret_basic", "client_secret_post", "none"}:
                raise ValueError("unsupported OIDC token endpoint authentication method")
            if self.token_auth_method != "none" and not self.client_secret:
                raise ValueError("OIDC token authentication requires a client secret")
        return self

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_origin}/api/v1/auth/callback"


class InvalidLogin(ValueError):
    pass


class OidcClient:
    def __init__(self, http: httpx.AsyncClient, settings: AuthSettings):
        try:
            import jwt
            from jwt.algorithms import has_crypto
        except ImportError as exc:
            raise RuntimeError("OIDC requires PyJWT[crypto]") from exc
        if not has_crypto:
            raise RuntimeError("OIDC requires PyJWT[crypto]")
        self.jwt = jwt
        self.http = http
        self.settings = settings

    @staticmethod
    def _object(response: httpx.Response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise InvalidLogin
        return payload

    async def discover(self) -> dict:
        issuer = self.settings.issuer
        metadata = self._object(
            await self.http.get(
                f"{issuer.rstrip('/')}/.well-known/openid-configuration",
                follow_redirects=False,
            )
        )
        if metadata.get("issuer") != issuer:
            raise InvalidLogin
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(metadata.get(field), str):
                raise InvalidLogin
            https_url(metadata[field])
        return metadata

    async def exchange(self, metadata: dict, *, code: str, verifier: str, nonce: str) -> dict:
        jwt = self.jwt
        config = self.settings
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": verifier,
        }
        auth = None
        if config.token_auth_method == "client_secret_basic":
            # The Basic credentials use OAuth's form-url-encoded client ID and secret.
            auth = (
                quote_plus(config.client_id),
                quote_plus(config.client_secret.get_secret_value()),
            )
        else:
            data["client_id"] = config.client_id
            if config.token_auth_method == "client_secret_post":
                data["client_secret"] = config.client_secret.get_secret_value()
        token_reply = self._object(
            await self.http.post(
                metadata["token_endpoint"],
                data=data,
                auth=auth,
                follow_redirects=False,
            )
        )
        token = token_reply.get("id_token")
        if not isinstance(token, str):
            raise InvalidLogin
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            supported = {
                "RS256",
                "RS384",
                "RS512",
                "PS256",
                "PS384",
                "PS512",
                "ES256",
                "ES384",
                "ES512",
            }
            if not isinstance(algorithm, str) or algorithm not in supported:
                raise InvalidLogin
            jwks = self._object(await self.http.get(metadata["jwks_uri"], follow_redirects=False))
            keys = jwks.get("keys")
            if not isinstance(keys, list):
                raise InvalidLogin
            keys = [
                key
                for key in keys
                if isinstance(key, dict)
                and key.get("kid") == header.get("kid")
                and key.get("use", "sig") == "sig"
                and key.get("alg", algorithm) == algorithm
            ]
            if len(keys) != 1 or keys[0].get("kty") not in ("RSA", "EC"):
                raise InvalidLogin
            key = jwt.PyJWK.from_dict(keys[0], algorithm=algorithm)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                issuer=config.issuer,
                audience=config.client_id,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            )
        except (jwt.PyJWTError, TypeError, KeyError, OverflowError) as exc:
            raise InvalidLogin from exc
        audience = claims["aud"]
        subject = claims["sub"]
        if (
            audience not in (config.client_id, [config.client_id])
            or ("azp" in claims and claims["azp"] != config.client_id)
            or claims["nonce"] != nonce
            or not isinstance(subject, str)
            or not subject
            or not subject.isascii()
            or len(subject) > 255
            or any(
                type(claims[field]) not in (int, float) or not isfinite(claims[field])
                for field in ("iat", "exp")
            )
        ):
            raise InvalidLogin
        return claims


def authorization_url(endpoint: str, params: dict[str, str]) -> str:
    parts = urlsplit(endpoint)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in params]
    return urlunsplit(parts._replace(query=urlencode([*query, *params.items()])))
