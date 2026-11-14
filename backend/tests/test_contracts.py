from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter, ValidationError

from travelmind.runtime.state import ResearchState, merge_records
from travelmind.schemas.agents import AgentFailure, AgentInput, AgentOutput
from travelmind.schemas.domain import ResearchBrief, ResearchPlan
from travelmind.schemas.events import AgentEvent


def test_plan_rejects_cycles():
    first, second = uuid4(), uuid4()
    with pytest.raises(ValidationError, match="cycle"):
        ResearchPlan.model_validate(
            {
                "objective": "研究泉州",
                "assignments": [
                    {
                        "assignment_id": first,
                        "agent_id": "history",
                        "objective": "梳理古城",
                        "depends_on": [second],
                    },
                    {
                        "assignment_id": second,
                        "agent_id": "resources",
                        "objective": "整理景点",
                        "depends_on": [first],
                    },
                ],
            }
        )


def test_agent_input_and_failure_output_round_trip():
    agent_input = AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id="director",
        brief={"destination": "泉州", "topics": ["历史"]},
    )
    result = AgentFailure(
        run_id=agent_input.run_id,
        agent_id=agent_input.agent_id,
        kind="failed",
        error={"code": "PROVIDER_TIMEOUT", "message": "调用超时", "retryable": True},
    )
    assert AgentInput.model_validate_json(agent_input.model_dump_json()) == agent_input
    assert TypeAdapter(AgentOutput).validate_json(result.model_dump_json()) == result


def test_event_rejects_payload_that_does_not_match_its_type():
    event = {
        "task_id": uuid4(),
        "trace_id": uuid4(),
        "event_id": uuid4(),
        "sequence": 1,
        "occurred_at": datetime.now(UTC),
        "payload": {"type": "evidence.added", "status": "queued"},
    }
    with pytest.raises(ValidationError):
        AgentEvent.model_validate(event)


def test_parallel_langgraph_nodes_merge_distinct_results():
    first = AgentFailure(
        run_id=uuid4(),
        agent_id="history",
        kind="failed",
        error={"code": "NO_RESULTS", "message": "查无资料", "retryable": False},
    )
    second = first.model_copy(update={"run_id": uuid4(), "agent_id": "resources"})
    builder = StateGraph(ResearchState)
    builder.add_node("history", lambda _: {"results": {first.run_id: first}})
    builder.add_node("resources", lambda _: {"results": {second.run_id: second}})
    builder.add_edge(START, "history")
    builder.add_edge(START, "resources")
    builder.add_edge("history", END)
    builder.add_edge("resources", END)
    graph = builder.compile()

    state = graph.invoke(
        {"task_id": uuid4(), "trace_id": uuid4(), "brief": ResearchBrief(destination="泉州")}
    )
    assert state["results"] == {first.run_id: first, second.run_id: second}


def test_reducer_can_replay_records_and_rejects_conflicts():
    record_id = uuid4()
    assert merge_records({record_id: "a"}, {record_id: "a"}) == {record_id: "a"}
    with pytest.raises(ValueError, match="conflicting"):
        merge_records({record_id: "a"}, {record_id: "b"})
