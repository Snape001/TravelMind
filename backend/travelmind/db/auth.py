import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import LoginFlowRow, LogoutReceiptRow, SessionRow, UserRow


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
    email: str | None = None
    email_verified: bool | None = None
    avatar_url: str | None = None


@dataclass(frozen=True)
class OidcSession:
    issuer: str
    client_id: str
    sid: str

    async def lock(self, session: AsyncSession) -> None:
        digest = token_hash("\0".join((self.issuer, self.client_id, self.sid)))
        await session.execute(
            select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True)))
        )


class SessionBindingInvalid(ValueError):
    pass


async def create_login_flow(
    session: AsyncSession, *, lifetime: int, user: AuthenticatedUser | None = None
) -> LoginFlow:
    flow = LoginFlow(*(token_urlsafe(32) for _ in range(4)))
    await session.execute(delete(LoginFlowRow).where(LoginFlowRow.expires_at <= func.now()))
    session.add(
        LoginFlowRow(
            state_hash=token_hash(flow.state),
            browser_hash=token_hash(flow.browser_token),
            nonce=flow.nonce,
            verifier=flow.verifier,
            expires_at=func.now() + timedelta(seconds=lifetime),
            expected_user_id=user.user_id if user else None,
            source_session_hash=user.session_hash if user else None,
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
    email: str | None = None,
    email_verified: bool | None = None,
    avatar_url: str | None = None,
    oidc: OidcSession | None = None,
    profile_flow: LoginFlowRow | None = None,
) -> str:
    if oidc:
        await oidc.lock(session)
        logged_out = await session.scalar(
            select(LogoutReceiptRow.jti)
            .where(
                LogoutReceiptRow.issuer == oidc.issuer,
                LogoutReceiptRow.client_id == oidc.client_id,
                LogoutReceiptRow.sid == oidc.sid,
                or_(LogoutReceiptRow.subject == subject, LogoutReceiptRow.subject.is_(None)),
                LogoutReceiptRow.expires_at > func.now(),
            )
            .limit(1)
        )
        if logged_out is not None:
            raise SessionBindingInvalid
    if profile_flow:
        await require_profile_session(session, profile_flow, issuer, subject, old_token)

    statement = insert(UserRow).values(
        user_id=uuid4(),
        issuer=issuer,
        subject=subject,
        display_name=display_name,
        email=email,
        email_verified=email_verified,
        avatar_url=avatar_url,
    )
    user_id = await session.scalar(
        statement.on_conflict_do_update(
            constraint="uq_users_identity",
            set_={
                field: statement.excluded[field]
                for field in ("display_name", "email", "email_verified", "avatar_url")
            },
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
            oidc_client_id=oidc.client_id if oidc else None,
            oidc_sid=oidc.sid if oidc else None,
        )
    )
    return token


async def require_profile_session(
    session: AsyncSession, flow: LoginFlowRow, issuer: str, subject: str, token: str | None
) -> None:
    if not token or token_hash(token) != flow.source_session_hash:
        raise SessionBindingInvalid
    bound = await session.scalar(
        select(SessionRow.user_id)
        .join(UserRow, UserRow.user_id == SessionRow.user_id)
        .where(
            SessionRow.token_hash == flow.source_session_hash,
            UserRow.user_id == flow.expected_user_id,
            UserRow.issuer == issuer,
            UserRow.subject == subject,
            *active_sessions(),
        )
        .with_for_update(of=SessionRow)
    )
    if bound is None:
        raise SessionBindingInvalid


async def revoke_oidc_sessions(
    session: AsyncSession,
    oidc: OidcSession,
    *,
    jti: str,
    subject: str | None,
    expires_at: datetime,
) -> None:
    await oidc.lock(session)
    receipt = insert(LogoutReceiptRow).values(
        issuer=oidc.issuer,
        client_id=oidc.client_id,
        jti=jti,
        sid=oidc.sid,
        subject=subject,
        expires_at=expires_at,
    )
    inserted = await session.scalar(
        receipt.on_conflict_do_nothing().returning(LogoutReceiptRow.jti)
    )
    if inserted is None:
        return
    owners = select(UserRow.user_id).where(UserRow.issuer == oidc.issuer)
    if subject is not None:
        owners = owners.where(UserRow.subject == subject)
    await session.execute(
        update(SessionRow)
        .where(
            SessionRow.oidc_client_id == oidc.client_id,
            SessionRow.oidc_sid == oidc.sid,
            SessionRow.user_id.in_(owners),
            SessionRow.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    await session.execute(delete(LogoutReceiptRow).where(LogoutReceiptRow.expires_at <= func.now()))


async def revoke_session(session: AsyncSession, digest: bytes) -> None:
    await session.execute(
        update(SessionRow)
        .where(SessionRow.token_hash == digest, SessionRow.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


def active_sessions():
    return (SessionRow.revoked_at.is_(None), SessionRow.expires_at > func.now())


async def read_session(
    session: AsyncSession,
    token: str,
    *,
    account_client: tuple[str, str] | None = None,
) -> AuthenticatedUser | None:
    digest = token_hash(token)
    statement = (
        select(UserRow)
        .join(SessionRow, SessionRow.user_id == UserRow.user_id)
        .where(SessionRow.token_hash == digest, *active_sessions())
    )
    if account_client is not None:
        issuer, client_id = account_client
        statement = statement.where(
            UserRow.issuer == issuer,
            SessionRow.oidc_client_id == client_id,
            SessionRow.oidc_sid.is_not(None),
        )
    user = await session.scalar(statement)
    if user is None:
        return None
    return AuthenticatedUser(
        user.user_id,
        user.display_name,
        digest,
        csrf_token(token),
        user.email,
        user.email_verified,
        user.avatar_url,
    )


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
