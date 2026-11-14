import asyncio
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError
from sqlalchemy import func, update

from travelmind.db.models import TaskRow
from travelmind.db.tasks import events_after
from travelmind.db.worker import PostgresWorkerStore
from travelmind.schemas.api import PendingClarification
from travelmind.schemas.worker import (
    AttemptFailure,
    LeaseLost,
    ProjectedEvent,
    ProjectionBatch,
    ProjectionConflict,
    ResumePause,
)

pytestmark = pytest.mark.postgres


def test_pause_resume_and_replay_snapshot(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert task.status_code == 201
    url = task.headers["location"]
    malformed = client.post(url + "/pause")
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "VALIDATION_ERROR"
    unwanted_body = client.post(url + "/pause", json={}, headers={"Idempotency-Key": "pause"})
    assert unwanted_body.status_code == 422
    paused = client.post(url + "/pause", headers={"Idempotency-Key": "pause"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    resumed = client.post(url + "/resume", headers={"Idempotency-Key": "resume"})
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    repeat = client.post(url + "/pause", headers={"Idempotency-Key": "pause"})
    assert repeat.status_code == paused.status_code
    assert repeat.json() == paused.json()
    conflict = client.post(url + "/resume", headers={"Idempotency-Key": "other"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "TASK_STATE_CONFLICT"


def test_clarification_answer_normalizes_order_and_deduplicates(client):
    task = client.post("/api/v1/tasks", json={"destination": "古城"})
    assert task.status_code == 201
    task_id = UUID(task.json()["task_id"])
    pending = PendingClarification.model_validate(
        {
            "clarification_id": uuid4(),
            "questions": [
                {"question_id": uuid4(), "text": "哪座古城？"},
                {"question_id": uuid4(), "text": "重点是什么？"},
            ],
        }
    )

    async def publish():
        store = PostgresWorkerStore(client.app.state.sessions)
        claim = await store.claim(uuid4(), lease_seconds=30)
        assert claim.lease.task_id == task_id
        await store.commit_projection(
            claim.lease,
            ProjectionBatch(
                checkpoint_id="clarification",
                projection_index=1,
                transition="clarification_required",
                interrupt_id="interrupt-1",
                clarification=pending,
            ),
        )

    client.portal.call(publish)
    url = task.headers["location"]
    assert client.get(url).json()["pending_clarification"] == pending.model_dump(mode="json")
    endpoint = f"{url}/clarifications/{pending.clarification_id}/answer"
    headers = {"Idempotency-Key": "answer"}
    answers = [
        {"question_id": str(question.question_id), "text": "  泉州  "}
        for question in pending.questions
    ]
    invalid = client.post(endpoint, json={"answers": answers[:1]}, headers=headers)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "VALIDATION_ERROR"
    accepted = client.post(endpoint, json={"answers": answers}, headers=headers)
    assert accepted.status_code == 202
    assert accepted.json()["pending_clarification"] is None
    for answer in answers:
        answer["text"] = answer["text"].strip()
    replay = client.post(endpoint, json={"answers": answers[::-1]}, headers=headers)
    assert replay.json() == accepted.json()
    answers[0]["text"] = "永定"
    conflict = client.post(endpoint, json={"answers": answers}, headers=headers)
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    stale = client.post(endpoint, json={"answers": answers}, headers={"Idempotency-Key": "new"})
    assert stale.json()["detail"]["code"] == "CLARIFICATION_STALE"


def test_claim_fences_stale_worker_and_replays_projection(client):
    task = client.post("/api/v1/tasks", json={"destination": "天津"})
    task_id = UUID(task.json()["task_id"])

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        first_claims = await asyncio.gather(
            store.claim(uuid4(), lease_seconds=30), store.claim(uuid4(), lease_seconds=30)
        )
        [first] = [claim for claim in first_claims if claim is not None]
        assert first.lease.task_id == task_id
        await store.renew(first.lease, lease_seconds=60)
        batch = ProjectionBatch(checkpoint_id="first", projection_index=1)
        assert await store.commit_projection(first.lease, batch) == "applied"
        assert await store.commit_projection(first.lease, batch) == "already_applied"
        with pytest.raises(ProjectionConflict):
            await store.commit_projection(
                first.lease, batch.model_copy(update={"projection_index": 3})
            )
        async with sessions.begin() as session:
            await session.execute(
                update(TaskRow).where(TaskRow.task_id == task_id).values(lease_until=func.now())
            )
        with pytest.raises(LeaseLost):
            async with sessions.begin() as session:
                await store.check_lease(first.lease, transaction=session)
        reclaimed = await store.claim(uuid4(), lease_seconds=30)
        assert reclaimed.reclaimed
        assert reclaimed.lease.lease_epoch == first.lease.lease_epoch + 1
        assert (await store.load_instruction(reclaimed.lease)).projected_index == 1
        with pytest.raises(LeaseLost):
            await store.commit_projection(first.lease, batch)

    client.portal.call(run)


def test_running_pause_resumes_from_the_persisted_interrupt(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    url = task.headers["location"]
    store = PostgresWorkerStore(client.app.state.sessions)

    async def claim():
        return (await store.claim(uuid4(), lease_seconds=30)).lease

    lease = client.portal.call(claim)
    assert str(lease.task_id) == task.json()["task_id"]
    paused = client.post(url + "/pause", headers={"Idempotency-Key": "pause-running"})
    assert paused.status_code == 202
    assert paused.json()["status"] == "running"
    assert paused.json()["pause_requested_at"]
    another = client.post(url + "/pause", headers={"Idempotency-Key": "repeat-pause"})
    assert another.json()["pause_requested_at"] == paused.json()["pause_requested_at"]

    async def reach_boundary():
        command = await store.load_instruction(lease)
        assert command.pause_requested_at
        await store.commit_projection(
            lease,
            ProjectionBatch(
                checkpoint_id="paused",
                projection_index=1,
                transition="pause_reached",
                interrupt_id="paused-interrupt",
            ),
        )
        with pytest.raises(LeaseLost):
            await store.load_instruction(lease)

    client.portal.call(reach_boundary)
    assert client.get(url).json()["status"] == "paused"
    resumed = client.post(url + "/resume", headers={"Idempotency-Key": "resume"})
    assert resumed.status_code == 202

    async def verify_resume():
        fresh = await claim()
        instruction = await store.load_instruction(fresh)
        assert isinstance(instruction.instruction, ResumePause)
        assert instruction.instruction.interrupt_id == "paused-interrupt"
        assert instruction.pause_requested_at is None
        async with client.app.state.sessions() as session:
            events = await events_after(session, fresh.task_id, 0)
            assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
            assert [event.payload.status for event in events] == [
                "queued",
                "running",
                "paused",
                "queued",
                "running",
            ]

    client.portal.call(verify_resume)


def test_retry_backoff_retains_attempt_budget_across_queued_pause(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    url = task.headers["location"]
    store = PostgresWorkerStore(client.app.state.sessions, max_attempts=2)

    async def fail_first_attempt():
        claim = await store.claim(uuid4(), lease_seconds=30)
        await store.commit_projection(claim.lease, failure)
        assert await store.claim(uuid4(), lease_seconds=30) is None

    failure = AttemptFailure.model_validate(
        {"failure_id": uuid4(), "error": {"code": "RETRY", "message": "retry", "retryable": True}}
    )
    client.portal.call(fail_first_attempt)
    assert (
        client.post(url + "/pause", headers={"Idempotency-Key": "pause-retry"}).status_code == 200
    )
    assert (
        client.post(url + "/resume", headers={"Idempotency-Key": "resume-retry"}).status_code == 202
    )

    async def fail_second_attempt():
        claim = await store.claim(uuid4(), lease_seconds=30)
        assert await store.commit_projection(claim.lease, failure) == "already_applied"
        await store.commit_projection(
            claim.lease, failure.model_copy(update={"failure_id": uuid4()})
        )

    client.portal.call(fail_second_attempt)
    assert client.get(url).json()["status"] == "failed"


def test_projection_makes_report_readable_and_rejects_conflicting_events(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    task_id = UUID(task.json()["task_id"])
    event_id, report_id = uuid4(), uuid4()

    async def run():
        store = PostgresWorkerStore(client.app.state.sessions)
        claim = await store.claim(uuid4(), lease_seconds=30)
        event = ProjectedEvent.model_validate(
            {
                "projection_key": event_id,
                "payload": {
                    "type": "report.ready",
                    "run_id": uuid4(),
                    "agent_id": "writer",
                    "report_id": report_id,
                    "version": 1,
                },
            }
        )
        batch = ProjectionBatch(checkpoint_id="report", projection_index=1, events=[event])
        with pytest.raises(ProjectionConflict):
            await store.commit_projection(claim.lease, batch)
        assert (await store.load_instruction(claim.lease)).projected_index == 0
        batch = ProjectionBatch.model_validate(
            {
                **batch.model_dump(),
                "reports": [
                    {
                        "task_id": task_id,
                        "report_id": report_id,
                        "version": 1,
                        "created_at": task.json()["created_at"],
                        "content": {
                            "title": "泉州",
                            "sections": [
                                {
                                    "heading": "概况",
                                    "body": "简介",
                                    "author_agent_ids": ["writer"],
                                }
                            ],
                        },
                    }
                ],
            }
        )
        assert await store.commit_projection(claim.lease, batch) == "applied"
        repeated_event = ProjectionBatch(checkpoint_id="next", projection_index=2, events=[event])
        assert await store.commit_projection(claim.lease, repeated_event) == "applied"
        altered = event.model_copy(
            update={"payload": event.payload.model_copy(update={"version": 2})}
        )
        with pytest.raises(ProjectionConflict):
            await store.commit_projection(
                claim.lease,
                ProjectionBatch(
                    checkpoint_id="different",
                    projection_index=3,
                    events=[altered],
                ),
            )
        async with client.app.state.sessions() as session:
            events = await events_after(session, task_id, 0)
            assert [item.payload.type for item in events] == [
                "task.status",
                "task.status",
                "report.ready",
            ]

    client.portal.call(run)
    assert client.get(task.headers["location"] + f"/reports/{report_id}").status_code == 200


def test_redis_unavailable_does_not_lose_committed_task(client):
    class Outage:
        async def publish(self, channel, task_id):
            assert channel == "travelmind:worker:wakeup"
            sessions = client.app.state.sessions
            async with sessions() as session:
                assert await session.get(TaskRow, UUID(task_id)) is not None
            raise ConnectionError("simulated outage")

    client.app.state.redis = Outage()
    created = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert created.status_code == 201

    async def claim():
        store = PostgresWorkerStore(client.app.state.sessions)
        return await store.claim(uuid4(), lease_seconds=30)

    assert str(client.portal.call(claim).lease.task_id) == created.json()["task_id"]
