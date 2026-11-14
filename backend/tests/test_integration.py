import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_runtime import Model

from travelmind.db.connection import create_database_engine
from travelmind.db.models import AgentRunRow
from travelmind.db.tasks import events_after
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.engine import ToolResult
from travelmind.runtime.checkpoints import (
    FencedPostgresSaver,
    checkpoint_dsn,
    initialize_checkpoints,
)
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import Evidence

pytestmark = pytest.mark.postgres


@asynccontextmanager
async def worker_connections(settings):
    engine = create_database_engine(settings)
    try:
        await initialize_checkpoints(settings)
        async with AsyncConnectionPool(
            checkpoint_dsn(settings),
            min_size=1,
            max_size=3,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        ) as pool:
            yield async_sessionmaker(engine, expire_on_commit=False), pool
    finally:
        await engine.dispose()


def test_runtime_persists_report_evidence_and_replayable_events(client):
    created = client.post("/api/v1/tasks", json={"destination": "泉州", "depth": "quick"})
    assert created.status_code == 201
    location = created.headers["location"]
    task_id = UUID(created.json()["task_id"])
    evidence = Evidence(
        evidence_id=uuid4(),
        source_type="official",
        source_title="泉州世界遗产",
        source_url="https://whc.unesco.org/en/list/1561/",
        retrieved_at=datetime.now(UTC),
        excerpt="22 个遗产点反映了宋元泉州作为世界海洋商贸中心的繁荣。",
    )

    class Engine:
        async def search(self, query, *, sources, limit):
            assert "泉州" in query and sources and limit > 0
            return ToolResult(evidence=[evidence])

        async def scrape(self, _):
            raise AssertionError("quick research should not fetch full articles")

    async def run():
        model = Model(evidence)
        async with worker_connections(client.app.state.settings) as (sessions, pool):
            store = PostgresWorkerStore(sessions)
            workers = [
                DurableWorker(
                    store,
                    model,
                    lambda lease: FencedPostgresSaver(pool, store, lease),
                    retrieve=lambda lease: ResearchRetriever(
                        Engine(), sessions, store=store, lease=lease
                    ),
                )
                for _ in range(2)
            ]
            claims = await asyncio.gather(
                *(store.claim(worker.worker_id, lease_seconds=45) for worker in workers)
            )
            assert sum(claim is not None for claim in claims) == 1
            worker, claim = next(
                (worker, claim)
                for worker, claim in zip(workers, claims, strict=True)
                if claim is not None
            )
            assert claim.lease.task_id == task_id
            await worker.run_claim(claim)
            async with sessions() as session:
                events = await events_after(session, task_id, 0)
                replay = await events_after(session, task_id, 2)
                runs = (
                    await session.scalars(select(AgentRunRow).where(AgentRunRow.task_id == task_id))
                ).all()
        assert [request.agent_id for request, _ in model.calls].count("research_director") == 1
        assert replay == events[2:]
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert len({event.trace_id for event in events}) == 1
        assert events[-1].payload.status == "completed"
        assert sum(event.payload.type == "evidence.added" for event in events) == 1
        assert any(run.result and run.result["kind"] == "report" for run in runs)
        assert all(run.status == "completed" for run in runs)
        return next(event.payload for event in events if event.payload.type == "report.ready")

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        report_ready = runner.run(run())
    assert client.get(location).json()["status"] == "completed"
    report = client.get(f"{location}/reports/{report_ready.report_id}")
    assert report.status_code == 200
    assert report.json()["content"]["sections"][0]["evidence_ids"] == [str(evidence.evidence_id)]
    assert client.get(f"{location}/evidence/{evidence.evidence_id}").status_code == 200


def test_runtime_persists_clarification_and_stops(client):
    task = client.post("/api/v1/tasks", json={"destination": "古城"}).json()
    task_id = UUID(task["task_id"])

    async def director(request, _, response_type, *, context):
        assert request.agent_id == "research_director"
        assert context["clarification_answers"] == []
        return response_type.model_validate({"questions": ["请问是哪座古城？"]})

    async def run():
        async with worker_connections(client.app.state.settings) as (sessions, pool):
            store = PostgresWorkerStore(sessions)
            worker = DurableWorker(
                store, director, lambda lease: FencedPostgresSaver(pool, store, lease)
            )
            claim = await store.claim(worker.worker_id, lease_seconds=45)
            assert claim is not None and claim.lease.task_id == task_id
            await worker.run_claim(claim)
            async with sessions() as session:
                return [event.payload for event in await events_after(session, task_id, 0)]

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        payloads = runner.run(run())
    assert payloads[-2].questions == ["请问是哪座古城？"]
    assert payloads[-1].status == "needs_clarification"
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "needs_clarification"


def test_optional_scraping_timeout_keeps_search_evidence(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州", "depth": "deep"}).json()
    evidence = Evidence(
        evidence_id=uuid4(),
        source_type="official",
        source_title="泉州世界遗产",
        source_url="https://whc.unesco.org/en/list/1561/",
        retrieved_at=datetime.now(UTC),
        excerpt="泉州是宋元时期的海洋商贸中心。",
    )

    class SlowScraper:
        async def search(self, *_args, **_options):
            return ToolResult(evidence=[evidence])

        async def scrape(self, _):
            await asyncio.sleep(0.1)
            raise AssertionError("optional scraping should be cancelled")

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        claim = await store.claim(uuid4(), lease_seconds=45)
        assert claim is not None and claim.lease.task_id == UUID(task["task_id"])
        retrieve = ResearchRetriever(SlowScraper(), sessions, store=store, lease=claim.lease)
        request = AgentInput(
            task_id=task["task_id"],
            trace_id=task["trace_id"],
            run_id=uuid4(),
            agent_id="history_culture",
            brief=task["brief"],
            budget=RunBudget(timeout_seconds=0.1),
        )
        assert await retrieve(request) == [evidence]

    assert client.portal is not None
    client.portal.call(run)
