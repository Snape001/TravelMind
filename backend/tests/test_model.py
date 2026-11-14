import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from travelmind.runtime.execution import RunError, with_budget
from travelmind.runtime.model import create_structured_model
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import ResearchBrief


@pytest.mark.parametrize("failure", ["invalid_provider_response", "truncated"])
def test_incomplete_model_response_is_classified_and_retried(failure):
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

    request = AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id="research_director",
        brief={"destination": "泉州"},
    )
    model = create_structured_model(Chat(), model_name="deepseek-flash")

    async def execute():
        return await with_budget(lambda: model(request, "整理简报", ResearchBrief), RunBudget())

    try:
        result = asyncio.run(execute())
    except RunError as error:
        pytest.fail(f"unexpected terminal error: {error.info.code}")
    assert result.destination == "泉州"
    assert calls == 2


def test_structured_model_separates_task_instructions_and_untrusted_source_data():
    ordinary_note = "source content for serialization test"
    untrusted_context = {"related_notes": ["external follow-up"]}
    seen = []

    class Chat:
        def bind(self, **options):
            seen.append(options)
            return self

        async def ainvoke(self, messages):
            (system_role, system), (data_role, data) = messages
            assert (system_role, data_role) == ("system", "human")
            assert "整理简报" in system
            assert ordinary_note not in system
            assert "external follow-up" not in system
            payload = json.loads(data)
            assert payload["input"]["evidence"][0]["excerpt"] == ordinary_note
            assert payload["context"] == untrusted_context
            return AIMessage(content='{"destination":"泉州"}')

    request = AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id="research_director",
        brief={"destination": "泉州"},
        evidence=[
            {
                "evidence_id": uuid4(),
                "source_title": "Search result",
                "source_type": "web",
                "source_url": "https://example.com/article",
                "excerpt": ordinary_note,
                "retrieved_at": datetime.now(UTC),
            }
        ],
        budget=RunBudget(timeout_seconds=7, max_output_tokens=1234),
    )
    model = create_structured_model(Chat(), model_name="deepseek-flash")
    assert asyncio.run(model(request, "整理简报", ResearchBrief, context=untrusted_context))
    [options] = seen
    assert options["timeout"] == 7
    assert options["max_tokens"] == 1234
