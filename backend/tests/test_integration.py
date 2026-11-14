import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_runtime import Model

from travelmind.db.models import AgentRunRow
from travelmind.db.tasks import events_after
from travelmind.research.engine import ToolResult
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.runtime.worker import ResearchWorker
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import Evidence

pytestmark = pytest.mark.postgres


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
        sessions = client.app.state.sessions
        model = Model(evidence)
        worker = ResearchWorker(sessions, model, Engine())
        await asyncio.gather(worker.execute(task_id), worker.execute(task_id))
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

    assert client.portal is not None
    report_ready = client.portal.call(run)
    assert client.get(location).json()["status"] == "completed"
    report = client.get(f"{location}/reports/{report_ready.report_id}")
    assert report.status_code == 200
    assert report.json()["content"]["sections"][0]["evidence_ids"] == [str(evidence.evidence_id)]
    assert client.get(f"{location}/evidence/{evidence.evidence_id}").status_code == 200


def test_runtime_persists_clarification_and_stops(client):
    task = client.post("/api/v1/tasks", json={"destination": "古城"}).json()
    task_id = UUID(task["task_id"])

    async def director(request, _, response_type):
        assert request.agent_id == "research_director"
        return response_type.model_validate({"questions": ["请问是哪座古城？"]})

    async def run():
        sessions = client.app.state.sessions
        worker = ResearchWorker(sessions, director, None)
        await worker.execute(task_id)
        async with sessions() as session:
            return [event.payload for event in await events_after(session, task_id, 0)]

    assert client.portal is not None
    payloads = client.portal.call(run)
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
        retrieve = ResearchRetriever(SlowScraper(), client.app.state.sessions)
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
