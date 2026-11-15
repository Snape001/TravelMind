import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.db.auth import (
    OidcSession,
    SessionBindingInvalid,
    csrf_token,
    read_session,
    revoke_oidc_sessions,
    start_session,
)
from travelmind.db.models import LogoutReceiptRow, ReportRow, TaskRow, UserRow
from travelmind.db.tasks import append_event
from travelmind.schemas.events import ReportReadyEvent

pytestmark = pytest.mark.postgres


def test_account_reports_uses_latest_published_event_and_owner(client):
    issuer = "https://account.example"
    subject = str(uuid4())
    sessions = client.app.state.sessions

    async def new_user():
        async with sessions.begin() as session:
            return await start_session(
                session, issuer, subject, None, lifetime=3600, old_token=None
            )

    other_token = client.portal.call(new_user)
    other_headers = {
        "Cookie": f"{SESSION_COOKIE}={other_token}",
        "X-CSRF-Token": csrf_token(other_token),
    }
    time = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)

    def task(headers=None):
        response = client.post("/api/v1/tasks", json={"destination": "泉州"}, headers=headers)
        assert response.status_code == 201
        return UUID(response.json()["task_id"])

    async def save(task_id, version, title, published_at):
        async with sessions.begin() as session:
            row = await session.get(TaskRow, task_id, with_for_update=True)
            report_id = uuid4()
            content = {
                "title": title,
                "sections": [
                    {"heading": "资料", "body": "测试内容", "author_agent_ids": ["writer"]}
                ],
            }
            session.add(
                ReportRow(report_id=report_id, task_id=task_id, version=version, content=content)
            )
            if published_at is not None:
                await append_event(
                    session,
                    row,
                    ReportReadyEvent(
                        type="report.ready",
                        report_id=report_id,
                        version=version,
                        agent_id="writer",
                        run_id=uuid4(),
                    ),
                    now=published_at,
                )
            return report_id

    mine = task()
    old = client.portal.call(save, mine, 1, "历史发布", time - timedelta(minutes=1))
    newest = client.portal.call(save, mine, 2, "最新发布", time)
    client.portal.call(save, mine, 3, "草稿", None)
    tie = client.portal.call(save, task(), 1, "同期发布", time)
    legacy_task = task()
    legacy = client.portal.call(save, legacy_task, 1, "旧版", None)
    other = client.portal.call(save, task(other_headers), 1, "他人报告", time)
    try:
        link = "/api/v1/account/reports"
        assert client.get(link, headers={"Cookie": ""}).status_code == 401
        first = client.get(link, params={"limit": 1})
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        cursor = first.json()["next_cursor"]
        second = client.get(link, params={"limit": 1, "cursor": cursor}).json()
        expected = sorted((newest, tie), reverse=True)
        assert [first.json()["items"][0]["report_id"], second["items"][0]["report_id"]] == [
            str(identity) for identity in expected
        ]
        assert second["next_cursor"] is None
        assert first.json()["items"][0]["published_at"] == "2026-11-15T08:00:00Z"
        for params in ({"limit": 51}, {"limit": 0}, {"cursor": ""}, {"cursor": "not_a_cursor"}):
            bad = client.get(link, params=params)
            assert bad.status_code == 422 and bad.json()["detail"]["code"] == "VALIDATION_ERROR"
        assert [
            item["report_id"] for item in client.get(link, headers=other_headers).json()["items"]
        ] == [str(other)]
        another_page = client.get(link, headers=other_headers, params={"cursor": cursor}).json()
        assert all(item["report_id"] == str(other) for item in another_page["items"])
        assert client.get(f"/api/v1/tasks/{mine}/reports/{old}").status_code == 200
        assert client.get(f"/api/v1/tasks/{legacy_task}/reports/{legacy}").status_code == 200
        for path in (
            f"/api/v1/tasks/{mine}/reports/{old}",
            f"/api/v1/tasks/{mine}/events",
            f"/api/v1/tasks/{mine}/research-events",
        ):
            assert client.get(path, headers=other_headers).status_code == 404
    finally:

        async def cleanup():
            async with sessions.begin() as session:
                owner = select(UserRow.user_id).where(
                    UserRow.issuer == issuer, UserRow.subject == subject
                )
                await session.execute(delete(TaskRow).where(TaskRow.owner_id.in_(owner)))
                await session.execute(delete(UserRow).where(UserRow.user_id.in_(owner)))

        client.portal.call(cleanup)


@pytest.mark.parametrize("login_first", [True, False])
def test_backchannel_logout_and_sign_in_serialize(client, login_first):
    issuer = "https://account.example"
    subject, other_subject = str(uuid4()), str(uuid4())
    oidc = OidcSession(issuer, "account-tests", str(uuid4()))
    sessions = client.app.state.sessions
    same_email = "shared@example.test"

    async def sign_in(session, identity=oidc, user=subject):
        return await start_session(
            session,
            identity.issuer,
            user,
            None,
            lifetime=3600,
            old_token=None,
            email=same_email,
            email_verified=True,
            oidc=identity,
        )

    async def notify(session):
        await revoke_oidc_sessions(
            session,
            oidc,
            jti=str(uuid4()),
            subject=subject,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    async def check():
        unrelated = (
            (OidcSession(issuer, oidc.client_id, str(uuid4())), subject),
            (OidcSession(issuer, oidc.client_id, oidc.sid), other_subject),
            (OidcSession("https://other.example", oidc.client_id, oidc.sid), subject),
            (OidcSession(issuer, "another-client", oidc.sid), subject),
        )
        try:
            async with sessions.begin() as session:
                legacy = await start_session(
                    session, issuer, subject, None, lifetime=3600, old_token=None
                )
                kept = [await sign_in(session, identity, user) for identity, user in unrelated]
            contenders = asyncio.Event()

            async def compete():
                async with sessions.begin() as session:
                    contenders.set()
                    if login_first:
                        await notify(session)
                    else:
                        await sign_in(session)

            async with sessions.begin() as session:
                token = await sign_in(session) if login_first else None
                if not login_first:
                    await notify(session)
                second = asyncio.create_task(compete())
                await contenders.wait()
                await asyncio.sleep(0.05)
                assert not second.done()
            if login_first:
                await asyncio.wait_for(second, 3)
            else:
                with pytest.raises(SessionBindingInvalid):
                    await asyncio.wait_for(second, 3)
            async with sessions() as session:
                if token:
                    assert await read_session(session, token) is None
                assert await read_session(session, legacy) is not None
                assert (
                    await read_session(session, legacy, account_client=(issuer, oidc.client_id))
                    is None
                )
                users = [await read_session(session, token) for token in kept]
                assert all(user is not None and user.email == same_email for user in users)
                assert len({user.user_id for user in users}) == 3
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(LogoutReceiptRow)
                        .where(LogoutReceiptRow.sid == oidc.sid)
                    )
                    == 1
                )
        finally:
            async with sessions.begin() as session:
                await session.execute(
                    delete(LogoutReceiptRow).where(LogoutReceiptRow.sid == oidc.sid)
                )
                await session.execute(
                    delete(UserRow).where(UserRow.subject.in_((subject, other_subject)))
                )

    client.portal.call(check)
