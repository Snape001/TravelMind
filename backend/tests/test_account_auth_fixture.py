from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from qa.seed_account_reports import seed_reports
from travelmind.db.auth import start_session
from travelmind.db.models import EventRow, TaskRow, UserRow


@pytest.mark.postgres
def test_account_qa_seed_preserves_private_reports_and_publication_metadata(client):
    alice_id = UUID(client.get("/api/v1/auth/me").json()["user_id"])
    bob_subject = str(uuid4())

    async def seed():
        async with client.app.state.sessions.begin() as session:
            await start_session(
                session,
                "https://issuer.example",
                bob_subject,
                None,
                lifetime=3600,
                old_token=None,
            )
            bob_id = await session.scalar(
                select(UserRow.user_id).where(UserRow.subject == bob_subject)
            )
            return await seed_reports(session, alice_id, bob_id)

    async def cleanup():
        async with client.app.state.sessions.begin() as session:
            bob_id = select(UserRow.user_id).where(UserRow.subject == bob_subject)
            await session.execute(delete(TaskRow).where(TaskRow.owner_id.in_(bob_id)))
            await session.execute(delete(UserRow).where(UserRow.subject == bob_subject))

    try:
        fixture = client.portal.call(seed)
        latest, high, low, oldest = fixture["alice_index"]
        assert [item["title"] for item in (latest, high, low, oldest)] == [
            "QA Alice latest revision",
            "QA Alice tie high",
            "QA Alice tie low",
            "QA Alice oldest",
        ]
        assert latest["published_at"] > high["published_at"] == low["published_at"]
        assert high["published_at"] > oldest["published_at"]
        assert UUID(high["report_id"]) > UUID(low["report_id"])

        def report_path(item):
            return f"/api/v1/tasks/{item['task_id']}/reports/{item['report_id']}"

        for item in (*fixture["alice_index"], fixture["alice_previous"], fixture["alice_legacy"]):
            response = client.get(report_path(item))
            assert response.status_code == 200
            assert response.json()["content"]["title"] == item["title"]
            assert response.json()["version"] == item["version"]
            if item["published_at"] is not None:
                assert datetime.fromisoformat(response.json()["created_at"]) != (
                    datetime.fromisoformat(item["published_at"])
                )

        [bob] = fixture["bob_index"]
        response = client.get(report_path(bob))
        missing = client.get(f"/api/v1/tasks/{uuid4()}/reports/{uuid4()}")
        assert response.status_code == missing.status_code == 404
        assert response.json() == missing.json()

        async def publication_events(task_id):
            async with client.app.state.sessions() as session:
                return (
                    await session.scalars(
                        select(EventRow)
                        .where(EventRow.task_id == UUID(task_id))
                        .order_by(EventRow.sequence)
                    )
                ).all()

        events = client.portal.call(publication_events, latest["task_id"])
        published = [row for row in events if row.payload["type"] == "report.ready"]
        assert [row.payload["report_id"] for row in published] == [
            fixture["alice_previous"]["report_id"],
            latest["report_id"],
        ]
        assert published[-1].occurred_at.isoformat() == latest["published_at"]
        assert fixture["alice_draft"]["task_id"] == latest["task_id"]
        assert fixture["alice_draft"]["version"] == 3

        idle = fixture["alice_idle"]
        task = f"/api/v1/tasks/{idle['task_id']}"
        assert client.get(task).json()["status"] == "queued"
        assert client.get(f"{task}/evidence/{idle['evidence_id']}").status_code == 200
        assert client.get(f"{task}/research-sources/{idle['source_id']}").status_code == 200
    finally:
        client.portal.call(cleanup)
