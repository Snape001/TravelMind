import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import LoginFlowRow, SessionRow, UserRow


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def csrf_token(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"travelmind-csrf-v1", "sha256").hexdigest()


@dataclass(frozen=True)
class LoginFlow:
    state: str
    browser_token: str
    nonce: str
    verifier: str


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    display_name: str | None
    session_hash: bytes
    csrf_token: str


async def create_login_flow(session: AsyncSession, *, lifetime: int) -> LoginFlow:
    flow = LoginFlow(*(token_urlsafe(32) for _ in range(4)))
    await session.execute(delete(LoginFlowRow).where(LoginFlowRow.expires_at <= func.now()))
    session.add(
        LoginFlowRow(
            state_hash=token_hash(flow.state),
            browser_hash=token_hash(flow.browser_token),
            nonce=flow.nonce,
            verifier=flow.verifier,
            expires_at=func.now() + timedelta(seconds=lifetime),
        )
    )
    return flow


async def consume_login_flow(
    session: AsyncSession, state: str, browser_token: str
) -> LoginFlowRow | None:
    return await session.scalar(
        delete(LoginFlowRow)
        .where(
            LoginFlowRow.state_hash == token_hash(state),
            LoginFlowRow.browser_hash == token_hash(browser_token),
            LoginFlowRow.expires_at > func.now(),
        )
        .returning(LoginFlowRow)
    )


async def start_session(
    session: AsyncSession,
    issuer: str,
    subject: str,
    display_name: str | None,
    *,
    lifetime: int,
    old_token: str | None,
) -> str:
    statement = insert(UserRow).values(
        user_id=uuid4(), issuer=issuer, subject=subject, display_name=display_name
    )
    user_id = await session.scalar(
        statement.on_conflict_do_update(
            constraint="uq_users_identity", set_={"display_name": statement.excluded.display_name}
        ).returning(UserRow.user_id)
    )
    if old_token:
        await revoke_session(session, token_hash(old_token))
    token = token_urlsafe(32)
    session.add(
        SessionRow(
            token_hash=token_hash(token),
            user_id=user_id,
            expires_at=func.now() + timedelta(seconds=lifetime),
        )
    )
    return token


async def revoke_session(session: AsyncSession, digest: bytes) -> None:
    await session.execute(
        update(SessionRow)
        .where(SessionRow.token_hash == digest, SessionRow.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


def active_sessions():
    return (SessionRow.revoked_at.is_(None), SessionRow.expires_at > func.now())


async def read_session(session: AsyncSession, token: str) -> AuthenticatedUser | None:
    digest = token_hash(token)
    user = await session.scalar(
        select(UserRow)
        .join(SessionRow, SessionRow.user_id == UserRow.user_id)
        .where(SessionRow.token_hash == digest, *active_sessions())
    )
    if user is None:
        return None
    return AuthenticatedUser(user.user_id, user.display_name, digest, csrf_token(token))


async def session_is_active(session: AsyncSession, user: AuthenticatedUser) -> bool:
    return (
        await session.scalar(
            select(SessionRow.user_id).where(
                SessionRow.token_hash == user.session_hash,
                SessionRow.user_id == user.user_id,
                *active_sessions(),
            )
        )
        is not None
    )
