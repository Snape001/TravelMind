import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from redis.exceptions import ConnectionError
from sqlalchemy import func, update

from travelmind.db.models import TaskRow
from travelmind.db.tasks import create_task, events_after
from travelmind.db.worker import PostgresWorkerStore
from travelmind.schemas.api import PendingClarification
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.worker import (
    AttemptFailure,
    LeaseLost,
    ProjectedEvent,
    ProjectionBatch,
    ProjectionConflict,
    ResumePause,
)

pytestmark = pytest.mark.postgres


def test_concurrent_submission_creates_one_task_and_event(client):
    rejected = client.post("/api/v1/tasks", json={"destination": "   "})
    assert rejected.status_code == 422
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


async def expire_lease(sessions, task_id, *, ago=0):
    async with sessions.begin() as session:
        await session.execute(
            update(TaskRow)
            .where(TaskRow.task_id == task_id)
            .values(lease_until=func.clock_timestamp() - timedelta(seconds=ago))
        )


def create_with_budget(client, max_attempts):
    user_id = UUID(client.get("/api/v1/auth/me").json()["user_id"])

    async def create():
        async with client.app.state.sessions() as session:
            task, created = await create_task(
                session,
                ResearchBrief(destination="泉州"),
                None,
                owner_id=user_id,
                max_attempts=max_attempts,
            )
            assert created
            return task.task_id

    return client.portal.call(create)


def test_exhausted_reclaims_never_issue_another_execution_lease(client):
    task_id = create_with_budget(client, 1)

    async def run():
        sessions = client.app.state.sessions
        left = PostgresWorkerStore(sessions)
        right = PostgresWorkerStore(sessions)
        worker_ids = uuid4(), uuid4()

        async def race():
            claims = await asyncio.gather(
                left.claim(worker_ids[0], lease_seconds=30),
                right.claim(worker_ids[1], lease_seconds=30),
            )
            (claim,) = [result for result in claims if result is not None]
            return claim

        first = await race()
        assert (first.max_attempts, first.attempt_count, first.execution_permitted) == (1, 1, True)
        await right.renew(first.lease, lease_seconds=60)
        data = ProjectionBatch(checkpoint_id="committed-step", projection_index=1)
        async with sessions.begin() as session:
            await left.check_lease(first.lease, transaction=session)
        assert await right.commit_projection(first.lease, data) == "applied"

        previous = first
        for epoch in range(2, 5):
            await expire_lease(sessions, task_id)
            claim = await race()
            assert claim.reclaimed and claim.lease.lease_epoch == epoch
            assert (claim.max_attempts, claim.attempt_count, claim.execution_permitted) == (
                1,
                1,
                False,
            )
            with pytest.raises(LeaseLost):
                await left.commit_projection(previous.lease, data)
            with pytest.raises(LeaseLost):
                async with sessions.begin() as session:
                    await left.check_lease(claim.lease, transaction=session)
            with pytest.raises(LeaseLost):
                async with client.app.state.engine.begin() as conn:
                    await left.check_lease(claim.lease, transaction=conn)
            await right.renew(claim.lease, lease_seconds=30)
            assert (await right.load_instruction(claim.lease)).projected_index == 1
            assert await left.commit_projection(claim.lease, data) == "already_applied"
            previous = claim

        failure = AttemptFailure.model_validate(
            {
                "failure_id": uuid4(),
                "error": {
                    "code": "ATTEMPT_BUDGET_EXHAUSTED",
                    "message": "任务执行次数已达上限",
                    "retryable": False,
                },
            }
        )
        assert await left.commit_projection(previous.lease, failure) == "applied"
        with pytest.raises(LeaseLost):
            await left.commit_projection(previous.lease, failure)
        assert await right.claim(uuid4(), lease_seconds=30) is None
        async with sessions() as session:
            row = await session.get(TaskRow, task_id)
            assert (row.status, row.attempt_count, row.lease_execution_permitted) == (
                "failed",
                1,
                None,
            )
            events = await events_after(session, task_id, 0)
            assert [event.payload.status for event in events] == ["queued", "running", "failed"]
            assert events[-1].payload.error == failure.error

    client.portal.call(run)


@pytest.mark.parametrize("operation", ["pause", "resume"])
def test_control_rejects_a_body_without_buffering_the_rest(client, operation):
    created = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert created.status_code == 201

    async def body() -> AsyncIterator[bytes]:
        yield b"unexpected"
        raise AssertionError("control endpoint should stop reading after the first nonempty chunk")

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url=str(client.base_url),
            headers=client.headers,
            cookies=dict(client.cookies),
        ) as browser:
            return await browser.post(
                f"{created.headers['location']}/{operation}",
                headers={"Idempotency-Key": str(uuid4())},
                content=body(),
            )

    response = client.portal.call(send)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert client.get(created.headers["location"]).json()["status"] == "queued"


def test_pause_resume_and_replay_snapshot(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert task.status_code == 201
    url = task.headers["location"]
    malformed = client.post(url + "/pause")
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "VALIDATION_ERROR"
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
    task_id = create_with_budget(client, 1)
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
        await expire_lease(client.app.state.sessions, task_id)
        claim = await store.claim(uuid4(), lease_seconds=30)
        assert claim.reclaimed and not claim.execution_permitted
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
    url = f"/api/v1/tasks/{task_id}"
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

    async def verify_answer_budget():
        sessions = client.app.state.sessions
        async with sessions() as session:
            row = await session.get(TaskRow, task_id)
            assert row.attempt_count == 0 and row.lease_execution_permitted is None
        store = PostgresWorkerStore(sessions)
        lease = await store.claim(uuid4(), lease_seconds=30)
        assert lease.attempt_count == 1 and lease.execution_permitted
        instruction = (await store.load_instruction(lease.lease)).instruction
        assert instruction.clarification_id == pending.clarification_id

    client.portal.call(verify_answer_budget)
    for answer in answers:
        answer["text"] = answer["text"].strip()
    replay = client.post(endpoint, json={"answers": answers[::-1]}, headers=headers)
    assert replay.json() == accepted.json()
    answers[0]["text"] = "永定"
    conflict = client.post(endpoint, json={"answers": answers}, headers=headers)
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    stale = client.post(endpoint, json={"answers": answers}, headers={"Idempotency-Key": "new"})
    assert stale.json()["detail"]["code"] == "CLARIFICATION_STALE"

    async def verify_replay_preserves_budget():
        async with client.app.state.sessions() as session:
            assert (await session.get(TaskRow, task_id)).attempt_count == 1

    client.portal.call(verify_replay_preserves_budget)


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
        assert await store.claim(uuid4(), lease_seconds=30) is None
        await expire_lease(sessions, task_id, ago=3)
        reclaimed = await store.claim(uuid4(), lease_seconds=30)
        assert reclaimed.reclaimed
        assert (reclaimed.max_attempts, reclaimed.attempt_count, reclaimed.execution_permitted) == (
            3,
            2,
            True,
        )
        assert reclaimed.lease.lease_epoch == first.lease.lease_epoch + 1
        assert (await store.load_instruction(reclaimed.lease)).projected_index == 1
        with pytest.raises(LeaseLost):
            await store.commit_projection(first.lease, batch)
        await expire_lease(sessions, task_id, ago=3)
        assert await store.claim(uuid4(), lease_seconds=30) is None
        await expire_lease(sessions, task_id, ago=5)
        last = await store.claim(uuid4(), lease_seconds=30)
        assert last.reclaimed and last.lease.lease_epoch == 3
        assert (last.attempt_count, last.execution_permitted) == (3, True)
        await expire_lease(sessions, task_id)
        exhausted = await store.claim(uuid4(), lease_seconds=30)
        assert exhausted.reclaimed and exhausted.lease.lease_epoch == 4
        assert (exhausted.attempt_count, exhausted.execution_permitted) == (3, False)

    client.portal.call(run)


def test_running_pause_resumes_from_the_persisted_interrupt(client):
    task_id = create_with_budget(client, 1)
    url = f"/api/v1/tasks/{task_id}"
    store = PostgresWorkerStore(client.app.state.sessions)

    async def claim():
        return (await store.claim(uuid4(), lease_seconds=30)).lease

    lease = client.portal.call(claim)
    assert lease.task_id == task_id
    paused = client.post(url + "/pause", headers={"Idempotency-Key": "pause-running"})
    assert paused.status_code == 202
    assert paused.json()["status"] == "running"
    assert paused.json()["pause_requested_at"]
    another = client.post(url + "/pause", headers={"Idempotency-Key": "repeat-pause"})
    assert another.json()["pause_requested_at"] == paused.json()["pause_requested_at"]

    async def reach_boundary():
        command = await store.load_instruction(lease)
        assert command.pause_requested_at
        sessions = client.app.state.sessions
        await expire_lease(sessions, task_id)
        recovery = await store.claim(uuid4(), lease_seconds=30)
        assert not recovery.execution_permitted
        await store.commit_projection(
            recovery.lease,
            ProjectionBatch(
                checkpoint_id="paused",
                projection_index=1,
                transition="pause_reached",
                interrupt_id="paused-interrupt",
            ),
        )
        with pytest.raises(LeaseLost):
            await store.load_instruction(recovery.lease)
        async with sessions() as session:
            assert (await session.get(TaskRow, task_id)).attempt_count == 1

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
            row = await session.get(TaskRow, task_id)
            assert (row.attempt_count, row.lease_execution_permitted) == (1, True)

    client.portal.call(verify_resume)
    assert (
        client.post(url + "/resume", headers={"Idempotency-Key": "resume"}).json() == resumed.json()
    )


def test_retry_backoff_retains_attempt_budget_across_queued_pause(client):
    task_id = create_with_budget(client, 2)
    url = f"/api/v1/tasks/{task_id}"
    store = PostgresWorkerStore(client.app.state.sessions)

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
        competing_store = PostgresWorkerStore(client.app.state.sessions)
        claims = await asyncio.gather(
            store.claim(uuid4(), lease_seconds=30),
            competing_store.claim(uuid4(), lease_seconds=30),
        )
        (claim,) = [candidate for candidate in claims if candidate is not None]
        assert (claim.max_attempts, claim.attempt_count, claim.execution_permitted) == (2, 2, True)
        assert await store.commit_projection(claim.lease, failure) == "already_applied"
        await competing_store.commit_projection(
            claim.lease, failure.model_copy(update={"failure_id": uuid4()})
        )
        assert await store.claim(uuid4(), lease_seconds=30) is None
        async with client.app.state.sessions() as session:
            events = await events_after(session, claim.lease.task_id, 0)
            assert events[-1].payload.error.code == "ATTEMPT_BUDGET_EXHAUSTED"
            assert not events[-1].payload.error.retryable

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


def test_exhausted_worker_projects_unpublished_terminal_report_once(client):
    task_id = create_with_budget(client, 1)
    report_id = uuid4()

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        first = await store.claim(uuid4(), lease_seconds=30)
        batch = ProjectionBatch.model_validate(
            {
                "checkpoint_id": "approved-report-checkpoint",
                "projection_index": 1,
                "transition": "complete",
                "reports": [
                    {
                        "task_id": task_id,
                        "report_id": report_id,
                        "version": 1,
                        "created_at": first.lease_until,
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
                "events": [
                    {
                        "projection_key": uuid4(),
                        "payload": {
                            "type": "report.ready",
                            "run_id": uuid4(),
                            "agent_id": "writer",
                            "report_id": report_id,
                            "version": 1,
                        },
                    }
                ],
            }
        )
        await expire_lease(sessions, task_id)
        recovery = await store.claim(uuid4(), lease_seconds=30)
        assert recovery.reclaimed and not recovery.execution_permitted
        with pytest.raises(LeaseLost):
            await store.commit_projection(first.lease, batch)
        assert await store.commit_projection(recovery.lease, batch) == "applied"
        with pytest.raises(LeaseLost):
            await store.commit_projection(recovery.lease, batch)
        assert await store.claim(uuid4(), lease_seconds=30) is None
        async with sessions() as session:
            events = await events_after(session, task_id, 0)
            assert [(event.sequence, event.payload.type) for event in events] == [
                (1, "task.status"),
                (2, "task.status"),
                (3, "report.ready"),
                (4, "task.status"),
            ]
            row = await session.get(TaskRow, task_id)
            assert (row.status, row.attempt_count, row.projected_index) == ("completed", 1, 1)

    client.portal.call(run)
    report = client.get(f"/api/v1/tasks/{task_id}/reports/{report_id}")
    assert report.status_code == 200 and report.json()["version"] == 1


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
