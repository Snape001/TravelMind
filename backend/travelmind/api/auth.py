import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from travelmind.api.dependencies import SESSION_COOKIE, Sessions, SignedIn, WriteUser, api_error
from travelmind.api.oidc import (
    InvalidLogin,
    OidcClient,
    authorization_url,
    https_url,
    oidc_identifier,
)
from travelmind.db.auth import (
    AuthenticatedUser,
    OidcSession,
    SessionBindingInvalid,
    consume_login_flow,
    create_login_flow,
    require_profile_session,
    revoke_oidc_sessions,
    revoke_session,
    start_session,
)
from travelmind.db.models import LoginFlowRow
from travelmind.schemas.api import (
    BackchannelLogoutError,
    CurrentUser,
    ErrorResponse,
    ProfileEdit,
    ProviderLogout,
)

LOGIN_COOKIE = "__Host-travelmind-login"
LOGIN_SECONDS = 600
MAX_LOGOUT_FORM_BYTES = 16_384
logger = logging.getLogger(__name__)
AUTH_ERRORS = {
    401: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
router = APIRouter(prefix="/api/v1/auth", tags=["auth"], responses=AUTH_ERRORS)
COOKIE_OPTIONS = {"httponly": True, "secure": True, "samesite": "lax", "path": "/"}
LOCATION = {"Location": {"description": "Redirect URL", "schema": {"type": "string"}}}


def _provider(request: Request) -> OidcClient:
    provider = request.app.state.oidc
    if provider is None:
        raise _provider_error(request)
    return provider


def require_account(request: Request) -> None:
    if not request.app.state.auth_settings.account_enabled:
        raise api_error(404, "ACCOUNT_FEATURE_DISABLED", "账户管理功能尚未启用")


ACCOUNT_ONLY = [Depends(require_account)]


def _provider_error(request: Request):
    if request.app.state.auth_settings.account_enabled:
        return api_error(503, "AUTH_PROVIDER_UNAVAILABLE", "身份认证服务暂时不可用", retryable=True)
    return api_error(503, "DATABASE_UNAVAILABLE", "身份认证服务暂时不可用", retryable=True)


def _invalid_callback() -> HTTPException:
    return api_error(400, "AUTH_CALLBACK_INVALID", "登录回调无效或已过期")


async def _discover(request: Request, provider: OidcClient) -> dict:
    try:
        return await provider.discover()
    except (ValueError, httpx.HTTPError) as exc:
        logger.warning("oidc.discovery_failed type=%s", type(exc).__name__)
        raise _provider_error(request) from None


async def _begin_authorization(
    request: Request,
    sessions: Sessions,
    *,
    params: dict[str, str] | None = None,
    user: AuthenticatedUser | None = None,
) -> tuple[str, str]:
    provider = _provider(request)
    metadata = await _discover(request, provider)
    async with sessions.begin() as session:
        flow = await create_login_flow(session, lifetime=LOGIN_SECONDS, user=user)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    location = authorization_url(
        metadata["authorization_endpoint"],
        {
            "response_type": "code",
            "client_id": provider.settings.client_id,
            "redirect_uri": provider.settings.redirect_uri,
            "scope": "openid profile email"
            if provider.settings.account_enabled
            else "openid profile",
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **(params or {}),
        },
    )
    return location, flow.browser_token


@router.get(
    "/login",
    status_code=302,
    response_class=RedirectResponse,
    responses={302: {"headers": LOCATION}},
)
async def login(request: Request, sessions: Sessions) -> RedirectResponse:
    return await _login_redirect(request, sessions)


@router.get(
    "/register",
    status_code=302,
    response_class=RedirectResponse,
    dependencies=ACCOUNT_ONLY,
    responses={302: {"headers": LOCATION}, 404: {"model": ErrorResponse}},
)
async def register(request: Request, sessions: Sessions) -> RedirectResponse:
    return await _login_redirect(request, sessions, params={"prompt": "create"})


async def _login_redirect(
    request: Request, sessions: Sessions, *, params: dict[str, str] | None = None
) -> RedirectResponse:
    location, token = await _begin_authorization(request, sessions, params=params)
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(LOGIN_COOKIE, token, max_age=LOGIN_SECONDS, **COOKIE_OPTIONS)
    return response


@router.post(
    "/profile/edit",
    dependencies=ACCOUNT_ONLY,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def edit_profile(
    request: Request, response: Response, user: WriteUser, sessions: Sessions
) -> ProfileEdit:
    location, token = await _begin_authorization(
        request, sessions, user=user, params={"kc_action": "UPDATE_PROFILE"}
    )
    response.set_cookie(LOGIN_COOKIE, token, max_age=LOGIN_SECONDS, **COOKIE_OPTIONS)
    return ProfileEdit(authorization_url=location)


def _claim_text(value: object, *, limit: int) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        if 0 < len(value) <= limit and value.isprintable():
            return value
    return None


def _avatar(value: object) -> str | None:
    picture = _claim_text(value, limit=2048)
    if picture:
        try:
            return https_url(picture)
        except ValueError:
            pass
    return None


async def _exchange_callback(
    request: Request, sessions: Sessions, provider: OidcClient
) -> tuple[LoginFlowRow, dict]:
    params = request.query_params
    state, code = params.getlist("state"), params.getlist("code")
    issuer = params.getlist("iss")
    browser_token = request.cookies.get(LOGIN_COOKIE)
    try:
        if (
            len(state) != 1
            or len(state[0]) != 43
            or not browser_token
            or len(browser_token) != 43
            or len(code) != 1
            or not code[0]
            or "error" in params
            or (issuer and issuer != [provider.settings.issuer])
        ):
            raise InvalidLogin
        async with sessions.begin() as session:
            flow = await consume_login_flow(session, state[0], browser_token)
            if flow is None:
                raise InvalidLogin
            verifier, nonce = flow.verifier, flow.nonce
        metadata = (
            await _discover(request, provider)
            if provider.settings.account_enabled
            else await provider.discover()
        )
        claims = await provider.exchange(metadata, code=code[0], verifier=verifier, nonce=nonce)
    except httpx.HTTPError as exc:
        logger.warning("oidc.exchange_failed type=%s", type(exc).__name__)
        if provider.settings.account_enabled and (
            not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
        ):
            raise _provider_error(request) from None
        raise _invalid_callback() from None
    except ValueError as exc:
        logger.warning("oidc.callback_rejected type=%s", type(exc).__name__)
        raise _invalid_callback() from None
    return flow, claims


@router.get(
    "/callback",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: {"headers": LOCATION},
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def callback(request: Request, sessions: Sessions) -> RedirectResponse:
    provider = _provider(request)
    flow, claims = await _exchange_callback(request, sessions, provider)
    settings = provider.settings
    email = _claim_text(claims.get("email"), limit=320)
    verified = claims.get("email_verified")
    sid = claims.get("sid")
    identity = None
    if settings.account_enabled:
        if not email or verified is not True:
            raise api_error(403, "AUTH_EMAIL_UNVERIFIED", "请先验证邮箱再登录")
        if not oidc_identifier(sid):
            raise _invalid_callback()
        identity = OidcSession(claims["iss"], settings.client_id, sid)
    profile_edit = flow.expected_user_id is not None
    old_token = request.cookies.get(SESSION_COOKIE)
    token = None
    try:
        async with sessions.begin() as session:
            if profile_edit and not settings.account_enabled:
                raise SessionBindingInvalid
            if profile_edit and request.query_params.getlist("kc_action_status") == ["cancelled"]:
                await require_profile_session(
                    session, flow, claims["iss"], claims["sub"], old_token
                )
            else:
                token = await start_session(
                    session,
                    claims["iss"],
                    claims["sub"],
                    _claim_text(claims.get("name"), limit=512),
                    lifetime=settings.session_seconds,
                    old_token=old_token,
                    email=email,
                    email_verified=verified if type(verified) is bool else None,
                    avatar_url=_avatar(claims.get("picture")),
                    oidc=identity,
                    profile_flow=flow if profile_edit else None,
                )
    except SessionBindingInvalid:
        raise _invalid_callback() from None
    target = "account" if profile_edit else "workbench"
    response = RedirectResponse(f"{settings.public_origin}/{target}", status_code=302)
    if token:
        response.set_cookie(
            SESSION_COOKIE, token, max_age=settings.session_seconds, **COOKIE_OPTIONS
        )
    response.delete_cookie(LOGIN_COOKIE, **COOKIE_OPTIONS)
    return response


@router.get("/me")
async def me(request: Request, user: SignedIn) -> CurrentUser:
    return CurrentUser(
        user_id=user.user_id,
        display_name=user.display_name,
        csrf_token=user.csrf_token,
        email=user.email,
        email_verified=user.email_verified,
        avatar_url=user.avatar_url,
        account_management_enabled=request.app.state.auth_settings.account_enabled,
    )


@router.post("/logout", status_code=204, responses={403: {"model": ErrorResponse}})
async def logout(user: WriteUser, sessions: Sessions) -> Response:
    async with sessions.begin() as session:
        await revoke_session(session, user.session_hash)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, **COOKIE_OPTIONS)
    return response


@router.post(
    "/logout/oidc",
    dependencies=ACCOUNT_ONLY,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def oidc_logout(
    request: Request, response: Response, user: WriteUser, sessions: Sessions
) -> ProviderLogout:
    async with sessions.begin() as session:
        await revoke_session(session, user.session_hash)
    response.delete_cookie(SESSION_COOKIE, **COOKIE_OPTIONS)
    provider = request.app.state.oidc
    url = None
    if provider is not None:
        try:
            metadata = await provider.discover()
            endpoint = metadata.get("end_session_endpoint")
            if isinstance(endpoint, str):
                url = authorization_url(
                    https_url(endpoint),
                    {
                        "client_id": provider.settings.client_id,
                        "post_logout_redirect_uri": provider.settings.logout_redirect_uri,
                    },
                )
        except (ValueError, httpx.HTTPError) as exc:
            logger.warning("oidc.logout_provider_unavailable type=%s", type(exc).__name__)
    return ProviderLogout(provider_logout_url=url)


async def _read_logout_token(request: Request) -> str:
    kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if kind != "application/x-www-form-urlencoded":
        raise InvalidLogin
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_LOGOUT_FORM_BYTES:
            raise InvalidLogin
        body.extend(chunk)
    pairs = parse_qsl(
        body.decode("ascii"), max_num_fields=1, keep_blank_values=True, strict_parsing=True
    )
    if len(pairs) != 1 or pairs[0][0] != "logout_token" or not pairs[0][1]:
        raise InvalidLogin
    return pairs[0][1]


@router.post(
    "/backchannel-logout",
    dependencies=ACCOUNT_ONLY,
    response_class=Response,
    responses={400: {"model": BackchannelLogoutError}, 404: {"model": ErrorResponse}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "properties": {"logout_token": {"type": "string"}},
                        "required": ["logout_token"],
                    }
                }
            },
        }
    },
)
async def backchannel_logout(request: Request, sessions: Sessions) -> Response:
    provider = _provider(request)
    try:
        token = await _read_logout_token(request)
        claims = await provider.verify_logout(token)
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    except httpx.HTTPError as exc:
        logger.warning("oidc.logout_verification_unavailable type=%s", type(exc).__name__)
        raise _provider_error(request) from None
    async with sessions.begin() as session:
        await revoke_oidc_sessions(
            session,
            OidcSession(claims["iss"], provider.settings.client_id, claims["sid"]),
            jti=claims["jti"],
            subject=claims.get("sub"),
            # An in-flight callback can outlive the two-minute logout token.
            expires_at=datetime.fromtimestamp(claims["exp"], UTC) + timedelta(minutes=15),
        )
    return Response()
