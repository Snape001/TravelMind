import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from travelmind.db.evidence import save_evidence
from travelmind.db.models import EvidenceRow
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.engine import ToolResult
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence

pytestmark = pytest.mark.postgres


def make_evidence(url):
    return Evidence(
        evidence_id=uuid4(),
        source_type="web",
        source_title="泉州古城",
        source_url=url,
        source_locator="泉州旅游指南",
        retrieved_at=datetime.now(UTC),
        excerpt="泉州古城历史文化资料。",
    )


def test_save_evidence_concurrently_and_read_through_existing_api(client):
    first_task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    second_task = client.post("/api/v1/tasks", json={"destination": "杭州"})
    assert first_task.status_code == second_task.status_code == 201
    first_id = UUID(first_task.json()["task_id"])
    second_id = UUID(second_task.json()["task_id"])
    a = make_evidence("https://example.org/guide?utm_source=search#intro")
    b = make_evidence("https://example.org/guide")
    distinct = make_evidence("https://example.org/guide?lang=en")

    async def write(task_id, items):
        async with client.app.state.sessions() as session, session.begin():
            return await save_evidence(session, task_id, items)

    async def run():
        results = await asyncio.gather(write(first_id, [a, b, distinct]), write(first_id, [b]))
        stored, second = results
        first = stored[0]
        (other_task,) = await write(second_id, [first])
        async with client.app.state.sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(EvidenceRow).where(EvidenceRow.task_id == first_id)
            )
        return first, stored, second, other_task, count

    first, stored, second, other_task, count = client.portal.call(run)
    assert first == second[0]
    assert first.evidence_id in {a.evidence_id, b.evidence_id}
    assert [item.evidence_id for item in stored] == [first.evidence_id, distinct.evidence_id]
    assert count == 2
    assert other_task.evidence_id != first.evidence_id
    result = client.get(f"/api/v1/tasks/{first_id}/evidence/{first.evidence_id}")
    assert result.status_code == 200
    assert result.json() == {"task_id": str(first_id), **first.model_dump(mode="json")}
    assert client.get(f"/api/v1/tasks/{second_id}/evidence/{first.evidence_id}").status_code == 404


def test_save_evidence_reuses_legacy_rows_and_rolls_back_with_caller(client):
    task_id = UUID(client.post("/api/v1/tasks", json={"destination": "泉州"}).json()["task_id"])
    legacy_id = uuid4()
    article = make_evidence("https://doi.org/10.1000/example")
    other = make_evidence("https://example.org/other")

    async def run():
        async with client.app.state.sessions() as session, session.begin():
            session.add(
                EvidenceRow(
                    **{
                        **article.model_dump(),
                        "evidence_id": legacy_id,
                        "source_url": "http://dx.doi.org/10.1000/Example",
                    },
                    task_id=task_id,
                )
            )
        async with client.app.state.sessions() as session:
            await session.begin()
            result = await save_evidence(session, task_id, [article, other])
            await session.rollback()
        return result

    stored = client.portal.call(run)
    assert stored[0].evidence_id == legacy_id
    assert stored[1].evidence_id == other.evidence_id
    assert client.get(f"/api/v1/tasks/{task_id}/evidence/{legacy_id}").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/evidence/{other.evidence_id}").status_code == 404


def test_parallel_retrieval_shares_a_scrape_and_stores_one_evidence_id(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州", "depth": "deep"}).json()
    page = make_evidence("https://example.org/zh/guide")
    scrape_calls = []

    class Engine:
        search_calls = 0

        async def search(self, *_args, **_options):
            self.search_calls += 1
            return ToolResult(
                evidence=[
                    make_evidence(f"https://example.org/guide?utm_source={self.search_calls}")
                ]
            )

        async def scrape(self, url):
            scrape_calls.append(url)
            await asyncio.sleep(0)
            return ToolResult(evidence=[page])

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        claim = await store.claim(uuid4(), lease_seconds=30)
        assert claim is not None and claim.lease.task_id == UUID(task["task_id"])
        retrieve = ResearchRetriever(Engine(), sessions, store=store, lease=claim.lease)
        requests = [
            AgentInput(
                task_id=task["task_id"],
                trace_id=task["trace_id"],
                run_id=uuid4(),
                agent_id=agent_id,
                brief=task["brief"],
            )
            for agent_id in ("history_culture", "tourism_resources")
        ]
        return await asyncio.gather(*(retrieve(request) for request in requests))

    assert client.portal.call(run) == [[page], [page]]
    assert len(scrape_calls) == 1
