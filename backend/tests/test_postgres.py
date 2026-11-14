import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from travelmind.config import Settings
from travelmind.db.tasks import events_after
from travelmind.main import create_app

pytestmark = pytest.mark.postgres


@pytest.fixture
def client():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated migrated test database")
    api = create_app(Settings(_env_file=None, database_url=database_url, redis_url=None))
    with TestClient(api) as transport:
        yield transport


def test_concurrent_submission_creates_one_task_and_event(client):
    key = str(uuid4())
    brief = {"destination": "泉州", "topics": ["历史"], "depth": "deep"}

    def submit(_):
        return client.post("/api/v1/tasks", json=brief, headers={"Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(submit, range(4)))
    assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
    task = replies[0].json()
    location = replies[0].headers["location"]
    assert all(reply.json() == task and reply.headers["location"] == location for reply in replies)
    assert client.get(location).json() == task

    async def read_events():
        async with client.app.state.sessions() as session:
            return await events_after(session, UUID(task["task_id"]), 0)

    assert client.portal is not None
    events = client.portal.call(read_events)
    assert len(events) == 1
    assert events[0].sequence == 1
    assert str(events[0].trace_id) == task["trace_id"]
    assert events[0].payload.model_dump(exclude_none=True) == {
        "type": "task.status",
        "status": "queued",
    }

    conflict = client.post(
        "/api/v1/tasks", json={"destination": "西安"}, headers={"Idempotency-Key": key}
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_missing_resources_and_invalid_cursor(client):
    missing = f"/api/v1/tasks/{uuid4()}"
    assert client.get(missing).status_code == 404
    assert client.get(f"{missing}/events").status_code == 404
    response = client.post("/api/v1/tasks", json={"destination": "   "})
    assert response.status_code == 422
    task = client.post("/api/v1/tasks", json={"destination": "杭州"})
    assert task.status_code == 201
    location = task.headers["location"]
    assert client.get(f"{location}/events?after=-1").status_code == 422
    assert client.get(f"{location}/reports/{uuid4()}").status_code == 404
    assert client.get(f"{location}/evidence/{uuid4()}").status_code == 404
