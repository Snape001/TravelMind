import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_durable_runtime import Store
from test_runtime import Model, research_input, source

from travelmind.runtime.checkpoints import checkpoint_serializer
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.runtime.reflection import ReflectionConfig
from travelmind.runtime.worker_cli import reflection_for
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import RunBudget
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.research_quality import ExpertResearchResult, grade_evidence
from travelmind.schemas.worker import ResumePause

QUESTION = "有多少处遗产点？"


def second_source(first, *, host="example.org", excerpt="泉州有 23 处遗产点。"):
    return type(first).model_validate(
        first.model_dump()
        | {"evidence_id": uuid4(), "source_url": f"https://{host}/quanzhou/", "excerpt": excerpt}
    )


class Scenario:
    def __init__(self, first, *, state="sufficient", follow_up=None, invalid_after_search=False):
        self.first = first
        self.state = state
        self.follow_up = follow_up
        self.invalid_after_search = invalid_after_search
        self.other = second_source(first)
        self.searches = []
        self.peers = []
        self.scored = []
        self.assessments = []
        self.writer_inputs = []
        self.reviewer_inputs = []
        self.model = Model(first, reply=self.reply)

    async def retrieve(self, request):
        agent, objective = request.agent_id, request.assignment.objective
        self.searches.append((agent, objective))
        if agent != "history_culture" or objective == "整理历史":
            return [self.first, self.other] if self.state == "conflicting" else [self.first]
        return self.follow_up if self.follow_up is not None else [self.first]

    async def score(self, request, *, as_of, max_age_days):
        self.scored.append((request.agent_id, as_of, max_age_days, request.evidence))
        cards = []
        for item in request.evidence:
            dims = {
                name: {"score": 2, "reason": "固定评分样本"}
                for name in ("traceability", "authority", "freshness", "relevance", "directness")
            }
            for dimension in ("relevance", "directness"):
                dims[dimension]["quote"] = item.excerpt
            if request.agent_id == "history_culture" and self.state == "low_quality":
                dims["directness"]["score"] = 0
            total, level = grade_evidence(
                *(item["score"] for item in dims.values()), max_age_days=max_age_days
            )
            cards.append(
                dict(
                    evidence_id=item.evidence_id,
                    assignment_id=request.assignment.assignment_id,
                    as_of=as_of,
                    max_age_days=max_age_days,
                    model_id="fixture-quality",
                    **dims,
                    total_score=total,
                    level=level,
                )
            )
        return cards

    async def reply(self, request, prompt, context):
        if request.agent_id == "writer":
            self.writer_inputs.append(context["contributions"])
        if request.agent_id == "reviewer":
            self.reviewer_inputs.append(context)
        if "答复定向质询" in prompt:
            self.peers.append(request.agent_id)
            return {"findings": [{"statement": "适用范围待核对", "support": "insufficient"}]}
        if "局部反思" not in prompt:
            return None
        agent = request.agent_id
        self.assessments.append(agent)
        state = self.state if agent == "history_culture" else "sufficient"
        after_search = agent == "history_culture" and any(
            name == agent and query != "整理历史" for name, query in self.searches
        )
        if after_search and self.invalid_after_search:
            return {
                "questions": [
                    {"question": "替换成了另一个问题", "reason": "不合法", "related_ids": [uuid4()]}
                ]
            }
        first = self.first.evidence_id
        judgment = dict(
            question=QUESTION, reason="核对原文和范围", finding_index=0, related_ids=[first]
        )
        if state == "sufficient":
            judgment["original_id"] = first
        if state == "conflicting":
            judgment["related_ids"].append(self.other.evidence_id)
            judgment["conflict_ids"] = [first, self.other.evidence_id]
        return {"questions": [judgment]}

    def config(self, **kwargs):
        return ReflectionConfig(model_id="fixture-reflection", score=self.score, **kwargs)


def report(values, agent="history_culture"):
    return next(
        item
        for raw in values["expert_research"].values()
        if (item := ExpertResearchResult.model_validate(raw)).research.agent_id == agent
    )


async def run_scenario(scenario, depth="deep", **limits):
    graph = build_research_graph(
        scenario.model,
        retrieve=scenario.retrieve,
        budget=RunBudget(max_attempts=1),
        reflection=scenario.config(**limits),
    )
    request = research_input()
    request["brief"] = request["brief"].model_copy(update={"depth": depth})
    return await graph.ainvoke(request)


@pytest.mark.parametrize(
    ("state", "strategy"),
    [
        ("sufficient", "accept"),
        ("insufficient", "expand_search"),
        ("low_quality", "replace_sources"),
        ("conflicting", "verify_conflict"),
    ],
)
def test_four_states_and_directed_search(state, strategy):
    scenario = Scenario(source(), state=state)
    values = asyncio.run(run_scenario(scenario))
    result = report(values)
    [initial] = [
        item
        for item in values["reflection_decisions"].values()
        if item["run_id"] == str(result.research.run_id) and item["action"]["round"] == 0
    ]
    assert initial["action"]["state"] == state
    assert initial["action"]["strategy"] == strategy
    assert values["status"] == "completed"
    targeted = [q for a, q in scenario.searches if a == "history_culture" and q != "整理历史"]
    if state == "sufficient":
        assert not targeted and not result.unresolved_questions
        assert result.reflection.stop_reason == "sufficient"
    else:
        assert len(targeted) == 1 and QUESTION in targeted[0]
        assert result.unresolved_questions == [QUESTION]
        assert result.reflection.stop_reason == "no_new_evidence"
        assert any(entry["agent_id"] == "history_culture" for entry in scenario.writer_inputs[0])
    if state == "conflicting":
        assert len(set(scenario.peers)) == len(scenario.peers) == 2
        assert "history_culture" not in scenario.peers
        assert result.research.findings[0].support == "disputed"
    else:
        assert not scenario.peers
    assert not any("交叉质询" in prompt for request, prompt in scenario.model.calls)


@pytest.mark.parametrize(
    ("depth", "limits", "stop", "round_number"),
    [
        ("quick", {}, "round_limit", 0),
        ("academic", {"max_follow_up_searches": 0}, "budget_exhausted", 0),
        ("academic", {"max_output_tokens": 8192}, "budget_exhausted", 0),
        ("academic", {}, "budget_exhausted", 1),
        ("deep", {}, "round_limit", 1),
    ],
)
def test_budget_round_limits_and_duplicate_queries(depth, limits, stop, round_number):
    first = source()
    scenario = Scenario(first, state="insufficient", follow_up=[second_source(first)])
    values = asyncio.run(run_scenario(scenario, depth, **limits))
    result = report(values)
    assert (result.reflection.stop_reason, result.reflection.round) == (stop, round_number)
    searches = [q for agent, q in scenario.searches if agent == "history_culture"]
    assert len(searches) == len(set(searches)) == round_number + 1
    times = {(t, window) for agent, t, window, _ in scenario.scored if agent == "history_culture"}
    assert len(times) == 1


def test_invalid_assessment_preserves_sources_and_records_gap():
    first = source()
    other = second_source(first)
    scenario = Scenario(first, state="insufficient", follow_up=[other], invalid_after_search=True)
    result = report(asyncio.run(run_scenario(scenario)))
    assert result.reflection.stop_reason == "assessment_unavailable"
    assert result.unresolved_questions == [QUESTION]
    assert result.research.evidence == [first, other]
    assert {card.evidence_id for card in result.quality} == {first.evidence_id, other.evidence_id}


def test_reflection_emits_one_expert_lifecycle_with_tools_inside_it():
    async def run():
        scenario = Scenario(source(), state="insufficient")
        graph = build_research_graph(
            scenario.model, retrieve=scenario.retrieve, reflection=scenario.config()
        )
        request = research_input()
        request["brief"] = request["brief"].model_copy(update={"depth": "deep"})
        events = [event async for event in graph.astream(request, stream_mode="custom")]
        event_types = [
            event.type
            for event in events
            if getattr(event, "agent_id", None) == "history_culture"
            and event.type != "evidence.added"
        ]
        assert event_types[0] == "agent.started"
        assert event_types[-1] == "agent.finished"
        assert event_types.count("agent.started") == event_types.count("agent.finished") == 1
        assert event_types.count("tool.started") == 2
        assert event_types.count("tool.finished") == 2
        assert scenario.reviewer_inputs[0]["expert_assessments"]

    asyncio.run(run())


def test_reflection_rejects_conflict_from_two_pages_on_the_same_source():
    first = source()
    scenario = Scenario(first, state="conflicting")
    scenario.other = second_source(first, host="whc.unesco.org")
    result = report(asyncio.run(run_scenario(scenario, depth="quick")))
    assert result.reflection.state == "insufficient"
    assert result.research.findings[0].support == "insufficient"
    assert not scenario.peers


def test_quality_review_revises_then_omits_persistently_low_grade_citations():
    class LowEvidence(Scenario):
        async def score(self, request, **context):
            return await super().score(
                request.model_copy(update={"agent_id": "history_culture"}), **context
            )

    scenario = LowEvidence(source(), state="low_quality")
    values = asyncio.run(run_scenario(scenario, depth="quick"))
    assert values["status"] == "completed"
    assert values["review"].decision == "approved"
    assert values["review_round"] == 2
    assert not any(section.evidence_ids for section in values["draft"].sections)
    assert any(
        issue.category == "citation" and issue.evidence_ids == [scenario.first.evidence_id]
        for result in values["results"].values()
        if result.kind == "review"
        for issue in result.review.issues
    )
    assert not scenario.reviewer_inputs


def test_academic_research_can_use_two_distinct_follow_ups():
    first = source()

    class Progressive(Scenario):
        async def reply(self, request, prompt, context):
            original = await super().reply(request, prompt, context)
            if request.agent_id == "history_culture" and "局部反思" in prompt:
                if len(request.evidence) > 1:
                    original["questions"][0]["related_ids"] += [
                        item.evidence_id for item in request.evidence if item != first
                    ]
            return original

        async def score(self, request, **context):
            cards = await super().score(request, **context)
            if request.agent_id == "history_culture":
                for card in cards:
                    if card["evidence_id"] != first.evidence_id:
                        card.update(
                            directness={
                                "score": 2,
                                "reason": "真实原文",
                                "quote": next(
                                    item.excerpt
                                    for item in request.evidence
                                    if item.evidence_id == card["evidence_id"]
                                ),
                            },
                            total_score=10,
                            level="high",
                        )
            return cards

    scenario = Progressive(first, state="low_quality")
    scenario.follow_up = [second_source(first)]
    result = report(asyncio.run(run_scenario(scenario, "academic")))
    assert (result.reflection.round, result.reflection.stop_reason) == (2, "no_new_evidence")
    searches = [q for agent, q in scenario.searches if agent == "history_culture"]
    assert len(set(searches)) == 3
    assert "官方" in searches[1] and "官方" not in searches[2]


def test_director_invites_two_peers_once_even_with_multiple_conflicts():
    class MultipleConflicts(Scenario):
        async def reply(self, request, prompt, context):
            response = await super().reply(request, prompt, context)
            if "局部反思" in prompt and request.agent_id == "tourism_resources":
                question = response["questions"][0]
                question.pop("original_id")
                question["conflict_ids"] = [self.first.evidence_id, self.other.evidence_id]
            return response

    scenario = MultipleConflicts(source(), state="conflicting")
    state = asyncio.run(run_scenario(scenario, "deep"))
    assert report(state).reflection.state == "conflicting"
    assert report(state, "tourism_resources").reflection.state == "conflicting"
    assert len(scenario.peers) == 2 and state["reflection_exchange_used"]


def test_provider_failure_preserves_previously_verified_findings():
    first = source()
    open_question = "何时可以参观？"

    class MissingAssessment(Scenario):
        async def reply(self, request, prompt, context):
            original = await super().reply(request, prompt, context)
            if request.agent_id == "history_culture" and "局部反思" in prompt:
                if context["required_questions"]:
                    raise TimeoutError
                original["questions"].append(
                    {
                        "question": open_question,
                        "reason": "缺少开放时间信息",
                    }
                )
            return original

    scenario = MissingAssessment(first, follow_up=[second_source(first)])
    result = report(asyncio.run(run_scenario(scenario, "deep")))
    assert result.reflection.stop_reason == "assessment_unavailable"
    assert result.unresolved_questions == [open_question]
    assert [item.support for item in result.research.findings] == ["supported", "insufficient"]
    assert result.research.findings[0].evidence_ids == [first.evidence_id]


def test_academics_can_exhaust_budget_before_any_retrieval():
    scenario = Scenario(source())
    result = report(asyncio.run(run_scenario(scenario, "academic", max_output_tokens=0)))
    assert result.reflection.stop_reason == "budget_exhausted"
    assert not scenario.searches and not scenario.scored


@pytest.mark.parametrize("execution_permitted", [True, False])
def test_worker_restarts_after_committed_reflection_without_repeating_search_or_exchange(
    execution_permitted,
):
    class Crashed(Exception):
        pass

    class FailingStore(Store):
        dropped = None

        async def commit_projection(self, lease, batch):
            if self.dropped is None and any(
                event.payload.type == "agent.message"
                and event.payload.message == "已完成第 1 轮补充核验。"
                for event in getattr(batch, "events", [])
            ):
                self.dropped = batch
                raise Crashed
            return await super().commit_projection(lease, batch)

    async def run():
        store = FailingStore("泉州")
        store.instruction = store.instruction.model_copy(
            update={
                "brief": ResearchBrief(destination="泉州", depth="deep"),
            }
        )
        scenario = Scenario(source(), state="conflicting")
        saver = InMemorySaver(serde=checkpoint_serializer())

        def worker():
            return DurableWorker(
                store,
                scenario.model,
                lambda _: saver,
                retrieve=lambda _: scenario.retrieve,
                reflection=scenario.config(),
            )

        with pytest.raises(Crashed):
            await worker().execute(store.claim())
        await worker().execute(store.claim(2, execution_permitted=execution_permitted))
        assert store.batches[store.dropped.projection_index - 1] == store.dropped
        if execution_permitted:
            assert store.batches[-1].transition == "complete"
        else:
            assert store.batches[-1] == store.dropped
            assert store.failures[-1].error.code == "ATTEMPT_BUDGET_EXHAUSTED"
        assert len(scenario.peers) == 2
        assert sum(agent == "history_culture" for agent, _ in scenario.searches) == 2

    asyncio.run(run())


def test_enabling_reflection_finishes_existing_v1_checkpoint_with_legacy_graph():
    class Crashed(Exception):
        pass

    class FailingStore(Store):
        dropped = None

        async def commit_projection(self, lease, batch):
            if self.dropped is None and any(
                event.payload.type == "plan.ready" for event in getattr(batch, "events", [])
            ):
                self.dropped = batch
                raise Crashed
            return await super().commit_projection(lease, batch)

    async def run():
        store = FailingStore("泉州")
        scenario = Scenario(source())
        saver = InMemorySaver(serde=checkpoint_serializer())
        args = (store, scenario.model, lambda _: saver)
        with pytest.raises(Crashed):
            await DurableWorker(*args, retrieve=lambda _: scenario.retrieve).execute(store.claim())
        await DurableWorker(
            *args, retrieve=lambda _: scenario.retrieve, reflection=scenario.config()
        ).execute(store.claim(2))
        assert store.batches[-1].transition == "complete"
        assert not scenario.assessments and not scenario.scored
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": str(store.task_id)}})
        assert checkpoint.metadata["graph_version"] == "durable-v1"

    asyncio.run(run())


def test_pause_before_follow_up_defers_retrieval_and_invited_peers():
    class PausingStore(Store):
        async def commit_projection(self, lease, batch):
            if any(
                event.payload.type == "agent.message"
                and event.payload.agent_id == "research_director"
                and "已邀请其他专家核对冲突来源" in event.payload.message
                for event in getattr(batch, "events", [])
            ):
                self.paused_at = datetime.now(UTC)
            return await super().commit_projection(lease, batch)

    async def run():
        store = PausingStore("泉州")
        store.instruction = store.instruction.model_copy(
            update={"brief": ResearchBrief(destination="泉州", depth="deep")},
        )
        scenario = Scenario(source(), state="conflicting")
        saver = InMemorySaver(serde=checkpoint_serializer())

        def worker():
            return DurableWorker(
                store,
                scenario.model,
                lambda _: saver,
                retrieve=lambda _: scenario.retrieve,
                reflection=scenario.config(),
            )

        await worker().execute(store.claim())
        batch = store.batches[-1]
        assert batch.transition == "pause_reached"
        assert not scenario.peers and len(scenario.searches) == 2
        store.instruction = ResumePause(
            kind="resume_pause",
            interrupt_id=batch.interrupt_id,
            instruction_id=uuid4(),
        )
        store.paused_at = None
        await worker().execute(store.claim(2))
        assert store.batches[-1].transition == "complete"
        assert len(scenario.peers) == 2
        assert sum(agent == "history_culture" for agent, _ in scenario.searches) == 2

    asyncio.run(run())


def test_worker_adapter_uses_the_evidence_quality_scorer_after_integration():
    first = source()
    semantic_calls = []

    class UsingScorer(Scenario):
        async def reply(self, request, prompt, context):
            if "输出一次独立评分" in prompt:
                semantic_calls.append(request.budget.max_attempts)
                return {
                    "assessments": [
                        {
                            "evidence_id": item["evidence_id"],
                            **{
                                key: {"score": 2, "reason": "原文直述", "quote": item["excerpt"]}
                                for key in ("relevance", "directness")
                            },
                        }
                        for item in context["evidence"]
                    ],
                }
            return await super().reply(request, prompt, context)

    async def run():
        scenario = UsingScorer(first)
        config = reflection_for(scenario.model, "integrated-quality-model")
        values = await build_research_graph(
            scenario.model, retrieve=scenario.retrieve, reflection=config
        ).ainvoke(research_input())
        result = report(values)
        assert result.reflection.model_id == "integrated-quality-model"
        assert result.reflection.state == "sufficient"
        assert len(result.quality) == 1 and result.quality[0].model_id == "integrated-quality-model"
        assert semantic_calls == [1, 1, 1]
        request = next(request for request, _ in scenario.model.calls if request.assignment)
        invalid = second_source(first, host="localhost", excerpt="点击查看详情")
        request = request.model_copy(update={"evidence": [invalid, first]})
        assert await config.rank(request, as_of=datetime.now(UTC), max_age_days=None) == [
            first,
            invalid,
        ]
        assert semantic_calls == [1, 1, 1]

    asyncio.run(run())
