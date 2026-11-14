import base64
import hashlib

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from travelmind.api.dependencies import SESSION_COOKIE, Sessions, SignedIn, WriteUser, api_error
from travelmind.api.oidc import InvalidLogin, authorization_url
from travelmind.db.auth import consume_login_flow, create_login_flow, revoke_session, start_session
from travelmind.schemas.api import CurrentUser, ErrorResponse

LOGIN_COOKIE = "__Host-travelmind-login"
LOGIN_SECONDS = 600
AUTH_ERRORS = {
    401: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
router = APIRouter(prefix="/api/v1/auth", tags=["auth"], responses=AUTH_ERRORS)
COOKIE_OPTIONS = {"httponly": True, "secure": True, "samesite": "lax", "path": "/"}
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
LOCATION = {"Location": {"description": "Redirect URL", "schema": {"type": "string"}}}


def _provider(request: Request):
    provider = request.app.state.oidc
    if provider is None:
        raise api_error(503, "DATABASE_UNAVAILABLE", "身份认证服务尚未配置", retryable=True)
    return provider


@router.get(
    "/login",
    status_code=302,
    response_class=RedirectResponse,
    responses={302: {"headers": LOCATION}},
)
async def login(request: Request, sessions: Sessions) -> RedirectResponse:
    provider = _provider(request)
    try:
        metadata = await provider.discover()
    except (ValueError, httpx.HTTPError):
        raise api_error(
            503, "DATABASE_UNAVAILABLE", "身份认证服务暂时不可用", retryable=True
        ) from None
    async with sessions.begin() as session:
        flow = await create_login_flow(session, lifetime=LOGIN_SECONDS)
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
            "scope": "openid profile",
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    response = RedirectResponse(location, status_code=302, headers=NO_STORE)
    response.set_cookie(LOGIN_COOKIE, flow.browser_token, max_age=LOGIN_SECONDS, **COOKIE_OPTIONS)
    return response


@router.get(
    "/callback",
    status_code=302,
    response_class=RedirectResponse,
    responses={302: {"headers": LOCATION}, 400: {"model": ErrorResponse}},
)
async def callback(request: Request, sessions: Sessions) -> RedirectResponse:
    provider = _provider(request)
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
        metadata = await provider.discover()
        claims = await provider.exchange(metadata, code=code[0], verifier=verifier, nonce=nonce)
    except (ValueError, httpx.HTTPError):
        raise api_error(400, "AUTH_CALLBACK_INVALID", "登录回调无效或已过期") from None

    name = claims.get("name")
    lifetime = provider.settings.session_seconds
    async with sessions.begin() as session:
        token = await start_session(
            session,
            claims["iss"],
            claims["sub"],
            name.strip() or None if isinstance(name, str) else None,
            lifetime=lifetime,
            old_token=request.cookies.get(SESSION_COOKIE),
        )
    response = RedirectResponse(
        f"{provider.settings.public_origin}/workbench", status_code=302, headers=NO_STORE
    )
    response.set_cookie(SESSION_COOKIE, token, max_age=lifetime, **COOKIE_OPTIONS)
    response.delete_cookie(LOGIN_COOKIE, **COOKIE_OPTIONS)
    return response


@router.get("/me")
async def me(user: SignedIn, response: Response) -> CurrentUser:
    response.headers.update(NO_STORE)
    return CurrentUser(
        user_id=user.user_id, display_name=user.display_name, csrf_token=user.csrf_token
    )


@router.post("/logout", status_code=204, responses={403: {"model": ErrorResponse}})
async def logout(user: WriteUser, sessions: Sessions) -> Response:
    async with sessions.begin() as session:
        await revoke_session(session, user.session_hash)
    response = Response(status_code=204, headers=NO_STORE)
    response.delete_cookie(SESSION_COOKIE, **COOKIE_OPTIONS)
    return response
