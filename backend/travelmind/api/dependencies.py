import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.auth import AuthenticatedUser, read_session

SESSION_COOKIE = "__Host-travelmind-session"
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


def api_error(status: int, code: str, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, "retryable": retryable})


def get_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions = request.app.state.sessions
    if sessions is None:
        raise api_error(503, "DATABASE_UNAVAILABLE", "任务数据库不可用", retryable=True)
    return sessions


Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessions)]


async def current_session(
    request: Request, sessions: Sessions, token: Annotated[str | None, Security(session_cookie)]
) -> AuthenticatedUser:
    if token:
        async with sessions() as session:
            user = await read_session(
                session, token, account_client=request.app.state.auth_settings.account_client
            )
        if user is not None:
            return user
    raise api_error(401, "UNAUTHENTICATED", "需要登录")


SignedIn = Annotated[AuthenticatedUser, Depends(current_session)]


async def require_csrf(
    request: Request,
    user: SignedIn,
    csrf: Annotated[
        str | None, Header(alias="X-CSRF-Token", description="Required for authenticated writes")
    ] = None,
) -> AuthenticatedUser:
    origin = request.app.state.auth_settings.public_origin
    origins = request.headers.getlist("origin")
    tokens = request.headers.getlist("x-csrf-token")
    if (
        origin is None
        or origins != [origin]
        or len(tokens) != 1
        or csrf is None
        or not hmac.compare_digest(csrf.encode("utf-8"), user.csrf_token.encode("ascii"))
    ):
        raise api_error(403, "CSRF_INVALID", "请求来源或 CSRF 令牌无效")
    return user


WriteUser = Annotated[AuthenticatedUser, Depends(require_csrf)]
