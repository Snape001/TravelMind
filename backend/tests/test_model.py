import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from travelmind.config import Settings
from travelmind.runtime.execution import RunError, with_budget
from travelmind.runtime.model import create_structured_model
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import ResearchBrief


@pytest.mark.parametrize("failure", ["invalid_provider_response", "truncated"])
def test_incomplete_model_response_is_classified_and_retried(monkeypatch, failure):
    calls = 0

    class Chat:
        def bind(self, **_options):
            return self

        async def ainvoke(self, _):
            nonlocal calls
            calls += 1
            if calls == 1:
                if failure == "invalid_provider_response":
                    raise ValueError("unexpected response type")
                return AIMessage(
                    content='{"destination":"旧值"}', response_metadata={"finish_reason": "length"}
                )
            return AIMessage(
                content='{"destination":"泉州"}', response_metadata={"finish_reason": "stop"}
            )

    monkeypatch.setattr("travelmind.runtime.model.create_chat_model", lambda *_: Chat())
    request = AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id="research_director",
        brief={"destination": "泉州"},
    )
    model = create_structured_model(Settings(_env_file=None))

    async def execute():
        return await with_budget(lambda: model(request, "整理简报", ResearchBrief), RunBudget())

    try:
        result = asyncio.run(execute())
    except RunError as error:
        pytest.fail(f"unexpected terminal error: {error.info.code}")
    assert result.destination == "泉州"
    assert calls == 2
