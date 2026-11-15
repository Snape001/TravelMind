from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import update
from test_runtime import source

from travelmind.db.models import TaskRow
from travelmind.db.worker import PostgresWorkerStore
from travelmind.runtime.model import create_structured_model
from travelmind.runtime.planning import DirectorDecision
from travelmind.runtime.research_telemetry import ResearchTelemetry, current_telemetry
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.worker import LeaseLost


@pytest.mark.postgres
def test_source_and_usage_are_fenced_replayable_and_owned(client):
    brief = ResearchBrief(destination="泉州")
    created = client.post("/api/v1/tasks", json=brief.model_dump(mode="json"))
    assert created.status_code == 201
    task_id = UUID(created.json()["task_id"])
    base = created.headers["Location"]
    item = source()

    class Chat:
        def bind(self, **_):
            return self

        async def ainvoke(self, messages):
            assert "private instructions" in messages[0][1]
            return AIMessage(
                content=DirectorDecision(brief=brief).model_dump_json(),
                usage_metadata={"input_tokens": 13, "output_tokens": 0, "total_tokens": 13},
                response_metadata={"model_name": "provider-reported-model"},
            )

    async def record():
        store = PostgresWorkerStore(client.app.state.sessions)
        claim = await store.claim(uuid4(), lease_seconds=30)
        request = AgentInput(
            task_id=task_id,
            trace_id=claim.trace_id,
            run_id=uuid4(),
            agent_id="research_director",
            brief=brief,
            budget=RunBudget(),
        )
        telemetry = ResearchTelemetry(store, claim.lease, claim.trace_id)
        ids = await telemetry.discover(request, [item, item])
        assert len(ids) == 1
        [source_id] = ids.values()
        attempt = await telemetry.start_fetch(request, source_id)
        await telemetry.finish_fetch(
            request,
            source_id,
            attempt,
            body="Actual fetched content",
            error_code=None,
        )
        token = current_telemetry.set(telemetry)
        try:
            await create_structured_model(Chat(), model_name="configured-model")(
                request, "private instructions", DirectorDecision, context={"secret": "private"}
            )
        finally:
            current_telemetry.reset(token)
        async with store.sessions.begin() as session:
            await session.execute(
                update(TaskRow).where(TaskRow.task_id == task_id).values(status="failed")
            )
        with pytest.raises(LeaseLost):
            await telemetry.start_fetch(request, source_id)
        return source_id

    source_id = client.portal.call(record)
    stream = client.get(f"{base}/research-events?after=0", headers={"Last-Event-ID": "1"})
    assert stream.status_code == 200
    assert "event: research.source.discovered" not in stream.text
    assert stream.text.count("event: research.source.fetch") == 2
    assert '"input_tokens":13,"output_tokens":0' in stream.text
    assert "provider-reported-model" in stream.text
    assert "private" not in stream.text
    assert "Actual fetched content" not in stream.text
    detail = client.get(f"{base}/research-sources/{source_id}")
    assert detail.json()["body_text"] == "Actual fetched content"
    assert client.get(f"{base}/research-sources/{uuid4()}").status_code == 404
    assert "research.source" not in client.get(f"{base}/events").text
