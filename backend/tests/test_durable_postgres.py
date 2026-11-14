import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_runtime import Model, source

from travelmind.config import Settings
from travelmind.db.connection import create_database_engine
from travelmind.db.control import apply_command
from travelmind.db.evidence import save_evidence
from travelmind.db.models import AgentRunRow, EventRow, ReportRow, TaskRow, UserRow
from travelmind.db.tasks import create_task, pending_clarification
from travelmind.db.worker import PostgresWorkerStore
from travelmind.runtime.checkpoints import (
    FencedPostgresSaver,
    checkpoint_dsn,
    initialize_checkpoints,
)
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.schemas.api import ClarificationAnswers
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.worker import LeaseLost, ProjectionBatch

pytestmark = pytest.mark.postgres


def test_real_worker_store_reclaims_terminal_checkpoint_and_fences_old_writes():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated migrated test database")

    class SimulatedCrash(Exception):
        pass

    class CrashStore(PostgresWorkerStore):
        dropped = None

        async def commit_projection(self, lease, projection):
            if isinstance(projection, ProjectionBatch) and projection.transition == "complete":
                self.dropped = projection
                raise SimulatedCrash
            return await super().commit_projection(lease, projection)

    async def run():
        settings = Settings(_env_file=None, database_url=url)
        engine = create_database_engine(settings)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        dsn = checkpoint_dsn(settings)
        await initialize_checkpoints(settings)
        task_id, owner_id, trace_id = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        item = source()
        model = Model(item)
        try:
            async with sessions.begin() as session:
                session.add(
                    UserRow(
                        user_id=owner_id,
                        issuer="https://issuer.example",
                        subject=str(owner_id),
                    )
                )
                await session.flush()
                session.add(
                    TaskRow(
                        task_id=task_id,
                        trace_id=trace_id,
                        owner_id=owner_id,
                        status="queued",
                        max_attempts=1,
                        created_at=now,
                        updated_at=now,
                        available_at=now,
                        brief={"destination": "泉州"},
                        instruction={
                            "kind": "start",
                            "instruction_id": str(uuid4()),
                            "brief": {"destination": "泉州"},
                        },
                    )
                )

            async with AsyncConnectionPool(
                dsn,
                min_size=1,
                max_size=6,
                open=False,
                kwargs={"autocommit": True, "row_factory": dict_row},
            ) as pool:
                store = CrashStore(sessions)
                claim = await store.claim(uuid4(), lease_seconds=30)
                assert claim.lease.task_id == task_id
                assert (claim.attempt_count, claim.max_attempts, claim.execution_permitted) == (
                    1,
                    1,
                    True,
                )

                def retrieve_for(lease):
                    async def retrieve(_):
                        async with sessions.begin() as session:
                            await store.check_lease(lease, transaction=session)
                            return await save_evidence(session, task_id, [item])

                    return retrieve

                stale = FencedPostgresSaver(pool, store, claim.lease)
                try:
                    worker = DurableWorker(store, model, lambda _: stale, retrieve=retrieve_for)
                    with pytest.raises(SimulatedCrash):
                        await worker.run_claim(claim)
                    assert store.dropped.reports
                    async with sessions.begin() as session:
                        await session.execute(
                            update(TaskRow)
                            .where(TaskRow.task_id == task_id)
                            .values(lease_until=func.clock_timestamp())
                        )
                    recovered = PostgresWorkerStore(sessions)
                    replacement = await recovered.claim(uuid4(), lease_seconds=30)
                    assert replacement.reclaimed
                    assert replacement.lease.lease_epoch == claim.lease.lease_epoch + 1
                    assert (
                        replacement.attempt_count,
                        replacement.max_attempts,
                        replacement.execution_permitted,
                    ) == (1, 1, False)

                    config = {
                        "configurable": {
                            "thread_id": str(task_id),
                            "checkpoint_ns": "",
                            "checkpoint_id": store.dropped.checkpoint_id,
                        }
                    }
                    with pytest.raises(LeaseLost):
                        await stale.aput(config, empty_checkpoint(), {"source": "loop"}, {})
                    with pytest.raises(LeaseLost):
                        await stale.aput_writes(config, [("test", "stale")], "old-worker")
                    projection_saver = FencedPostgresSaver(pool, recovered, replacement.lease)
                    with pytest.raises(LeaseLost):
                        await projection_saver.aput_writes(
                            config, [("test", "no execution permit")], "projection-worker"
                        )
                    await DurableWorker(
                        recovered,
                        model,
                        lambda _: projection_saver,
                        retrieve=retrieve_for,
                    ).run_claim(replacement)
                    async with sessions() as session:
                        task = await session.get(TaskRow, task_id)
                        assert task.status == "completed"
                        assert task.attempt_count == 1
                        assert task.projected_index == store.dropped.projection_index
                        runs = (
                            await session.scalars(
                                select(AgentRunRow).where(AgentRunRow.task_id == task_id)
                            )
                        ).all()
                        [report] = (
                            await session.scalars(
                                select(ReportRow).where(ReportRow.task_id == task_id)
                            )
                        ).all()
                        events = (
                            await session.scalars(
                                select(EventRow)
                                .where(EventRow.task_id == task_id)
                                .order_by(EventRow.sequence)
                            )
                        ).all()
                    assert report.report_id == store.dropped.reports[0].report_id
                    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
                    assert {event.trace_id for event in events} == {trace_id}
                    assert events[-1].payload["status"] == "completed"
                    assert sum(event.payload["type"] == "report.ready" for event in events) == 1
                    assert sum(event.payload["type"] == "evidence.added" for event in events) == 1
                    assert [request.agent_id for request, _ in model.calls].count("reviewer") == 1
                    assert {run.result["kind"] for run in runs if run.result} >= {
                        "report",
                        "review",
                    }
                finally:
                    await stale.adelete_thread(str(task_id))
        finally:
            try:
                async with sessions.begin() as session:
                    await session.execute(delete(TaskRow).where(TaskRow.task_id == task_id))
                    await session.execute(delete(UserRow).where(UserRow.user_id == owner_id))
            finally:
                await engine.dispose()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())


def test_pause_and_clarification_resume_from_postgres_with_new_worker_connections():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated migrated test database")

    async def run():
        settings = Settings(_env_file=None, database_url=url)
        engine = create_database_engine(settings)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresWorkerStore(sessions)
        await initialize_checkpoints(settings)
        owner_id = uuid4()
        item = source()
        task_id = None

        async def model_reply(request, _prompt, context):
            if request.agent_id == "research_director" and not context["clarification_answers"]:
                return {"questions": ["请问是哪座古城？"]}
            if request.agent_id == "planner":
                async with sessions() as session:
                    status, _, _ = await apply_command(
                        session, owner_id, task_id, "pause", "pause-after-plan"
                    )
                    assert status == 202
            return None

        model = Model(item, reply=model_reply)

        def retrieve_for(lease):
            async def retrieve(_):
                async with sessions.begin() as session:
                    await store.check_lease(lease, transaction=session)
                    return await save_evidence(session, task_id, [item])

            return retrieve

        async def work():
            async with AsyncConnectionPool(
                checkpoint_dsn(settings),
                min_size=1,
                max_size=6,
                open=False,
                kwargs={"autocommit": True, "row_factory": dict_row},
            ) as pool:
                worker = DurableWorker(
                    store,
                    model,
                    lambda lease: FencedPostgresSaver(pool, store, lease),
                    retrieve=retrieve_for,
                )
                claim = await store.claim(worker.worker_id, lease_seconds=30)
                assert claim.lease.task_id == task_id
                await worker.run_claim(claim)

        try:
            async with sessions.begin() as session:
                session.add(
                    UserRow(
                        user_id=owner_id, issuer="https://issuer.example", subject=str(owner_id)
                    )
                )
            async with sessions() as session:
                task, _ = await create_task(
                    session, ResearchBrief(destination="古城"), None, owner_id=owner_id
                )
                task_id = task.task_id
            await work()
            async with sessions() as session:
                pending = await pending_clarification(session, task_id)
                assert pending is not None
            answer = ClarificationAnswers(
                answers=[
                    {"question_id": pending.questions[0]["question_id"], "text": "泉州"},
                ]
            )
            async with sessions() as session:
                code, queued, _ = await apply_command(
                    session,
                    owner_id,
                    task_id,
                    "answer",
                    "answer",
                    clarification_id=pending.clarification_id,
                    answers=answer,
                )
                assert code == 202 and queued.status == "queued"
            await work()
            async with sessions() as session:
                task = await session.get(TaskRow, task_id)
                assert task.status == "paused" and task.paused_interrupt_id is not None
            async with sessions() as session:
                code, queued, _ = await apply_command(
                    session, owner_id, task_id, "resume", "resume"
                )
                assert code == 202 and queued.status == "queued"
            await work()
            async with sessions() as session:
                assert (await session.get(TaskRow, task_id)).status == "completed"
                events = (
                    await session.scalars(
                        select(EventRow)
                        .where(EventRow.task_id == task_id)
                        .order_by(EventRow.sequence)
                    )
                ).all()
            names = [request.agent_id for request, _ in model.calls]
            assert names.count("research_director") == 2
            assert names.count("planner") == 1
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            assert sum(event.payload["type"] == "task.clarification" for event in events) == 1
            assert sum(event.payload["type"] == "report.ready" for event in events) == 1
        finally:
            try:
                async with sessions.begin() as session:
                    await session.execute(delete(TaskRow).where(TaskRow.task_id == task_id))
                    await session.execute(delete(UserRow).where(UserRow.user_id == owner_id))
            finally:
                await engine.dispose()

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())
