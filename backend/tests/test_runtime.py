import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import AgentFailure, ResearchResult, RunBudget
from travelmind.schemas.domain import Evidence, ResearchBrief
from travelmind.schemas.events import AgentEvent


def research_input():
    return {
        "task_id": uuid4(),
        "trace_id": uuid4(),
        "brief": ResearchBrief(destination="泉州", topics=["历史文化", "景点", "出行"]),
    }


def source():
    return Evidence(
        evidence_id=uuid4(),
        source_type="official",
        source_title="泉州世界遗产",
        source_url="https://whc.unesco.org/en/list/1561/",
        retrieved_at=datetime.now(UTC),
        excerpt="22 个遗产点反映了宋元泉州作为世界海洋商贸中心的繁荣。",
    )


class Model:
    def __init__(self, evidence, *, reply=None):
        self.evidence = evidence
        self.reply = reply
        self.calls = []
        self.drafts = 0

    async def __call__(self, request, instructions, response_type, /):
        self.calls.append((request, instructions))
        if self.reply:
            response = await self.reply(request, instructions)
            if response is not None:
                return response_type.model_validate(response)
        finding = {
            "statement": "泉州有 22 个世界遗产点。",
            "evidence_ids": [self.evidence.evidence_id],
            "support": "supported",
        }
        if request.agent_id == "research_director":
            response = {"brief": request.brief.model_dump(), "questions": []}
        elif request.agent_id == "planner":
            response = {
                "objective": "了解泉州",
                "assignments": [
                    {"agent_id": "history_culture", "objective": "整理历史"},
                    {"agent_id": "tourism_resources", "objective": "甄选遗产点"},
                    {
                        "agent_id": "travel_planning",
                        "objective": "整合已甄选景点",
                        "depends_on": ["tourism_resources"],
                    },
                ],
            }
        elif request.agent_id == "writer":
            self.drafts += 1
            response = {
                "title": "泉州调研",
                "sections": [
                    {
                        "heading": "遗产点",
                        "body": f"泉州有 22 个遗产点。第 {self.drafts} 版。",
                        "author_agent_ids": ["tourism_resources"],
                        "evidence_ids": [self.evidence.evidence_id],
                    }
                ],
            }
        elif request.agent_id == "reviewer":
            response = {"decision": "approved"}
        elif "交叉质询" in instructions:
            response = {"finding_index": 0, "question": "22 个点是否都是单独景区？"}
        else:
            response = {"findings": [finding]}
        return response_type.model_validate(response)


def test_director_stops_for_clarification_and_can_run_with_an_amended_brief():
    evidence = source()

    async def reply(request, _):
        if request.agent_id == "research_director" and request.brief.destination == "古城":
            return {"questions": ["请问是哪座古城？"]}
        return None

    async def retrieve(_):
        return [evidence]

    async def run():
        model = Model(evidence, reply=reply)
        graph = build_research_graph(model, retrieve=retrieve)
        request = research_input()
        request["brief"] = ResearchBrief(destination="古城")
        first = await graph.ainvoke(request)
        assert first["status"] == "needs_clarification"
        assert first["questions"] == ["请问是哪座古城？"]
        assert [call.agent_id for call, _ in model.calls] == ["research_director"]
        request["brief"] = ResearchBrief(destination="泉州古城")
        second = await graph.ainvoke(request)
        assert second["status"] == "completed"

    asyncio.run(run())


def test_parallel_experts_preserve_successful_results_and_pass_dependency_context():
    evidence = source()

    async def run():
        both_started = asyncio.Event()
        started = set()

        async def retrieve(request):
            if request.agent_id in {"history_culture", "tourism_resources"}:
                started.add(request.agent_id)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 1)
            if request.agent_id == "history_culture":
                raise TimeoutError
            if request.agent_id == "travel_planning":
                assert request.prior_findings[0].evidence_ids == [evidence.evidence_id]
                assert request.evidence == [evidence]
            return [evidence]

        model = Model(evidence)
        graph = build_research_graph(model, retrieve=retrieve, budget=RunBudget(max_attempts=1))
        state = await graph.ainvoke(research_input())
        assert state["status"] == "completed"
        assignments = state["plan"].assignments
        assert assignments[2].depends_on == [assignments[1].assignment_id]
        history = state["results"][state["assignment_runs"][assignments[0].assignment_id]]
        assert isinstance(history, AgentFailure)
        assert history.error.retryable
        assert state["evidence"] == {evidence.evidence_id: evidence}
        cross_checks = [(req, prompt) for req, prompt in model.calls if req.assignment is None]
        questions = [(req, prompt) for req, prompt in cross_checks if "交叉质询" in prompt]
        replies = [(req, prompt) for req, prompt in cross_checks if "答复质询" in prompt]
        assert len(questions) == len(replies) == 2
        assert all(req.prior_findings and req.evidence == [evidence] for req, _ in questions)

    asyncio.run(run())


@pytest.mark.parametrize("approve_revision", [True, False])
def test_review_checks_the_draft_and_bounds_revision(approve_revision):
    evidence = source()

    async def retrieve(_):
        return [evidence]

    async def reply(request, instructions):
        if request.agent_id == "reviewer":
            assert request.draft is not None
            if "第 1 版" in request.draft.sections[0].body or not approve_revision:
                return {
                    "decision": "revision_required",
                    "issues": [{"category": "coverage", "message": "补充遗产范围"}],
                }
        if request.agent_id == "writer" and request.draft is not None:
            assert "补充遗产范围" in instructions
        return None

    async def run():
        model = Model(evidence, reply=reply)
        graph = build_research_graph(model, retrieve=retrieve, max_revision_rounds=1)
        state = await graph.ainvoke(research_input())
        assert model.drafts == 2
        assert state["review_round"] == 2
        assert state["status"] == ("completed" if approve_revision else "failed")
        assert state["review"].decision == ("approved" if approve_revision else "revision_required")

    asyncio.run(run())


def test_unknown_report_reference_cannot_be_approved_and_events_use_existing_contract():
    evidence = source()

    async def retrieve(_):
        return [evidence]

    async def reply(request, _):
        if request.agent_id == "writer":
            return {
                "title": "泉州调研",
                "sections": [
                    {
                        "heading": "遗产点",
                        "body": "有世界遗产点。",
                        "author_agent_ids": ["tourism_resources"],
                        "evidence_ids": [uuid4()],
                    }
                ],
            }
        return None

    async def run():
        request = research_input()
        graph = build_research_graph(
            Model(evidence, reply=reply), retrieve=retrieve, max_revision_rounds=0
        )
        events = [
            AgentEvent(
                event_id=uuid4(),
                sequence=sequence,
                occurred_at=datetime.now(UTC),
                task_id=request["task_id"],
                trace_id=request["trace_id"],
                payload=payload,
            )
            for sequence, payload in enumerate(
                [payload async for payload in graph.astream(request, stream_mode="custom")], 1
            )
        ]
        assert events[0].payload.status == "running"
        assert events[-1].payload.status == "failed"
        assert any(event.payload.type == "plan.ready" for event in events)
        assert any(event.payload.type == "tool.finished" for event in events)
        assert any(event.payload.type == "evidence.added" for event in events)
        reviews = [event.payload for event in events if event.payload.type == "report.reviewed"]
        assert [review.decision for review in reviews] == ["revision_required"]
        assert not any(event.payload.type == "report.ready" for event in events)

    asyncio.run(run())


def test_invalid_expert_references_are_isolated_and_exhausted_research_stops():
    evidence = source()

    async def retrieve(_):
        return [evidence]

    async def reply(request, _):
        if request.assignment:
            return {
                "findings": [
                    {
                        "statement": "无法追溯的结论",
                        "support": "supported",
                        "evidence_ids": [uuid4()],
                    }
                ]
            }
        return None

    async def run():
        model = Model(evidence, reply=reply)
        graph = build_research_graph(model, retrieve=retrieve, budget=RunBudget(max_attempts=1))
        state = await graph.ainvoke(research_input())
        assert state["status"] == "failed"
        assert state["failure"].code == "NO_RESEARCH_RESULTS"
        assert not any(isinstance(result, ResearchResult) for result in state["results"].values())
        assert not any(call.agent_id in {"writer", "reviewer"} for call, _ in model.calls)

    asyncio.run(run())


def test_planner_retries_a_cycle_then_dispatches_the_selected_experts():
    evidence = source()
    attempts = 0

    async def retrieve(_):
        return [evidence]

    async def reply(request, _):
        nonlocal attempts
        if request.agent_id != "planner":
            return None
        attempts += 1
        return {
            "objective": "调查泉州的环境、景点和游客反馈",
            "assignments": [
                {"agent_id": "natural_geography", "objective": "梳理地理环境"},
                {
                    "agent_id": "tourism_resources",
                    "objective": "分析景点",
                    "depends_on": ["social_sentiment"] if attempts == 1 else [],
                },
                {
                    "agent_id": "social_sentiment",
                    "objective": "核对景点评价",
                    "depends_on": ["tourism_resources"],
                },
            ],
        }

    async def run():
        model = Model(evidence, reply=reply)
        graph = build_research_graph(model, retrieve=retrieve)
        state = await graph.ainvoke(research_input())
        assert attempts == 2
        assert state["status"] == "completed"
        assert {call.agent_id for call, _ in model.calls if call.assignment} == {
            "natural_geography",
            "tourism_resources",
            "social_sentiment",
        }

    asyncio.run(run())


def test_failed_dependency_blocks_only_its_dependent_expert():
    evidence = source()

    async def retrieve(request):
        if request.agent_id == "tourism_resources":
            raise TimeoutError
        assert request.agent_id != "travel_planning"
        return [evidence]

    async def run():
        model = Model(evidence)

        async def reply(request, _):
            if request.agent_id == "writer":
                return {
                    "title": "泉州调研",
                    "sections": [
                        {
                            "heading": "遗产点",
                            "body": "现有资料涵盖历史，景点路线仍待核实。",
                            "author_agent_ids": ["history_culture"],
                            "evidence_ids": [evidence.evidence_id],
                        }
                    ],
                }
            return None

        model.reply = reply
        graph = build_research_graph(model, retrieve=retrieve, budget=RunBudget(max_attempts=1))
        state = await graph.ainvoke(research_input())
        assert state["status"] == "completed"
        failures = {
            output.agent_id: output.error.code
            for output in state["results"].values()
            if isinstance(output, AgentFailure)
        }
        assert failures == {
            "tourism_resources": "PROVIDER_TIMEOUT",
            "travel_planning": "DEPENDENCY_FAILED",
        }
        assert not any(call.agent_id == "travel_planning" for call, _ in model.calls)

    asyncio.run(run())
