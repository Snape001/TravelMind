import asyncio
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from test_runtime import Model, research_input, source

from travelmind.runtime import editorial
from travelmind.runtime.execution import InvalidOutput, RunError
from travelmind.runtime.planning import plan_research
from travelmind.runtime.skills import SkillRegistry
from travelmind.runtime.skills.contracts import EvidenceSynthesis, ReportOutline
from travelmind.runtime.skills.research import director_skill, research_skill
from travelmind.runtime.skills.writer import (
    catalog,
    require_draft,
    require_outline,
    require_synthesis,
)
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import AgentInput, ResearchResult
from travelmind.schemas.domain import Assignment, Finding, ReportDraft, ResearchBrief, Review


def test_loads_only_selected_version_and_preserves_profile_identity(monkeypatch):
    import travelmind.runtime.skills.registry as registry_module

    reads = []
    original = registry_module.read_definition

    def read(ref):
        reads.append(ref.skill_id)
        return original(ref)

    monkeypatch.setattr(registry_module, "read_definition", read)
    registry = SkillRegistry()
    assert reads == []
    assert registry.select("writer", "outline").skill_id == "report_architecture"
    assert registry.select("report_writer", "outline").skill_id == "report_architecture"
    assert reads == ["report_architecture"]
    with pytest.raises(RunError, match="SKILL_NOT_AVAILABLE"):
        registry.select("reviewer", "write")
    assert reads == ["report_architecture"]
    profiles = registry.profiles()
    assert len(profiles.experts) == 10
    assert {p.agent_id for p in profiles.experts} >= {
        "research_director",
        "history_culture",
        "natural_geography",
        "tourism_resources",
        "social_sentiment",
        "literature_research",
        "internet_research",
        "travel_planning",
        "report_writer",
        "reviewer",
    }
    assert reads == ["report_architecture"]


def test_all_profile_skill_refs_resolve_with_their_stage_budget():
    registry = SkillRegistry()
    profiles = {profile.agent_id: profile for profile in registry.profiles().experts}
    declared = {
        (profile.agent_id, ref.skill_id, ref.version)
        for profile in profiles.values()
        for ref in profile.skills
    }
    resolved = set()
    for role, stage, tool_calls in (
        ("research_director", "research", 0),
        ("history_culture", "research", 1),
        ("natural_geography", "research", 1),
        ("tourism_resources", "research", 1),
        ("social_sentiment", "research", 1),
        ("literature_research", "research", 1),
        ("internet_research", "research", 1),
        ("travel_planning", "plan_route", 1),
        ("report_writer", "outline", 0),
        ("report_writer", "synthesize", 0),
        ("report_writer", "write", 0),
        ("report_writer", "edit", 0),
        ("reviewer", "review", 0),
    ):
        definition = registry.select(role, stage)
        key = (role, definition.skill_id, definition.version)
        assert key in declared, (role, stage)
        assert definition.budget.tool_calls == tool_calls, (role, stage)
        assert definition.max_context_chars > definition.budget.model.max_output_tokens, key
        resolved.add(key)
    assert resolved == declared


def agent(agent_id, *, objective=None):
    return AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id=agent_id,
        brief=ResearchBrief(destination="济南", topics=["预约", "泉城文化"]),
        assignment=Assignment(assignment_id=uuid4(), agent_id=agent_id, objective=objective)
        if objective
        else None,
    )


def test_planner_can_select_lin_for_tourism_resources_without_route_planning():
    request = agent("planner")
    request.brief = ResearchBrief(destination="济南", topics=["景点取舍", "预约与开放条件"])

    async def model(input, instructions, output):
        assert input.brief.topics == ["景点取舍", "预约与开放条件"]
        assert "tourism_resources：旅游资源专家" in instructions
        assert "travel_planning" not in instructions
        return output(
            objective="核对景点与参观条件",
            assignments=[
                {"agent_id": "tourism_resources", "objective": "核对景点开放与预约条件"},
                {"agent_id": "history_culture", "objective": "复核景点的历史价值"},
                {"agent_id": "natural_geography", "objective": "核对景区的季节性限制"},
            ],
        )

    result = asyncio.run(plan_research(model, request, travel_requested=False))
    ids = [assignment.agent_id for assignment in result.plan.assignments]
    assert ids == ["tourism_resources", "history_culture", "natural_geography"]
    registry = SkillRegistry()
    profiles = {profile.agent_id: profile for profile in registry.profiles().experts}
    lin = profiles[ids[0]]
    assert (lin.name, lin.title, lin.tier) == ("林景川", "旅游资源专家", "council")
    assert "山海勘游使" in lin.description
    assert lin.portrait == "/images/experts/lin-jingchuan.webp"
    lu = profiles["travel_planning"]
    assert (lu.name, lu.portrait) == ("陆知行", "/images/experts/tourism_resources.webp")
    skill = registry.select(lin.agent_id, "research")
    assert (skill.skill_id, skill.version) == ("attraction_assessment", 1)


def test_director_and_expert_use_own_schema_and_task_tools():
    registry = SkillRegistry()
    calls = []

    async def model(request, instructions, output, *, context=None):
        calls.append((request.agent_id, instructions, output.__name__))
        if output.__name__ == "DirectorDecision":
            return output(brief=request.brief)
        return output(findings=[{"statement": "需核验局限", "support": "insufficient"}])

    async def retrieve(request):
        calls.append(("retrieve", request.agent_id))
        return []

    async def run():
        await director_skill(registry, model, agent("research_director"))
        request = agent("literature_research", objective="查找泉水与历史街区的文献")
        await research_skill(registry, model, request)

        async def in_graph(state):
            await research_skill(registry, model, request, retrieve=retrieve)
            return state

        graph = StateGraph(dict)
        graph.add_node("expert", in_graph).add_edge(START, "expert").add_edge("expert", END)
        await graph.compile().ainvoke({})
        with pytest.raises(RunError, match="SKILL_NOT_AVAILABLE"):
            await research_skill(registry, model, request.model_copy(update={"assignment": None}))

    asyncio.run(run())
    assert [call[0] for call in calls] == [
        "research_director",
        "literature_research",
        "retrieve",
        "literature_research",
    ]
    assert "书目" in calls[-1][1] and "游客样本" not in calls[-1][1]


def test_context_and_tool_allowance_are_hard_limits():
    registry = SkillRegistry()
    request = agent("writer")
    session = registry.session(request, "outline")
    with pytest.raises(RunError, match="SKILL_TOOL_DENIED"):
        session.take_tool("retrieve_evidence", {"retrieve_evidence"})
    request = agent("history_culture", objective="地方史")
    session = registry.session(request, "research")
    with pytest.raises(RunError, match="SKILL_TOOL_DENIED"):
        session.take_tool("retrieve_evidence", set())
    session.take_tool("retrieve_evidence", {"retrieve_evidence"})
    with pytest.raises(RunError, match="SKILL_TOOL_BUDGET"):
        session.take_tool("retrieve_evidence", {"retrieve_evidence"})

    async def oversized_model(*args, **kwargs):
        pytest.fail("an over-budget prompt must not reach the model")

    request = agent("research_director")
    with pytest.raises(RunError, match="SKILL_CONTEXT_BUDGET"):
        asyncio.run(director_skill(registry, oversized_model, request, [{"answer": "长" * 30000}]))


@pytest.mark.parametrize("revise", [False, True])
def test_writer_uses_topic_outline_then_synthesis_writing_and_edit_with_independent_review(
    monkeypatch,
    revise,
):
    evidence = source()
    recorder = []
    registry = SkillRegistry()
    base = Model(evidence)
    heading = "从泉港遗产到步行游览"
    usable = str(evidence.evidence_id)
    draft_body = "沿遗产点步行能把古代港口活动与街区空间联系起来。"
    edited_body = draft_body + "先选一个主点，停留时间按个人兴趣预留。"
    revisions = 0
    checked_revisions = []
    finalize = editorial.finalize_revision

    def check_revision(request, contributor_ids, **kwargs):
        assert request.draft.sections[0].body == edited_body
        assert request.draft.sections[0].evidence_ids == [evidence.evidence_id]
        checked_revisions.append(request.run_id)
        return finalize(request, contributor_ids, **kwargs)

    async def model(request, instructions, output, *, context=None):
        nonlocal revisions
        if request.agent_id not in {"writer", "reviewer"}:
            return await base(request, instructions, output, context=context)
        recorder.append((request.agent_id, output.__name__))
        assert request.budget.max_attempts <= 2
        if output.__name__ == "ReportOutline":
            assert "证据整合" not in instructions
            assert bool(context["revision_notes"]) == bool(revisions)
            findings = [item["id"] for item in context["findings"]]
            return output(
                title="泉州游览与遗产",
                sections=[
                    {"heading": heading, "purpose": "连接历史与旅行决策", "finding_ids": findings},
                ],
            )
        if output.__name__ == "EvidenceSynthesis":
            ids = context["outline"]["sections"][0]["finding_ids"]
            return output(
                sections=[
                    {
                        "heading": heading,
                        "points": [{"finding_ids": ids, "summary": "遗产与街区体验相关"}],
                        "gaps": ["预约待核"],
                        "conflicts": ["资料表述口径不同"],
                    }
                ]
            )
        if output is Review:
            assert request.draft.sections[0].body == edited_body
            assert "独立" in instructions and "停留" in instructions
            if revise and not revisions:
                revisions += 1
                return Review(
                    decision="revision_required",
                    issues=[{"category": "coverage", "message": "交代预约信息的核验缺口"}],
                )
            return Review(decision="approved")
        assert output.__name__ == "ReportDraft"
        return output(
            title="泉州游览与遗产",
            sections=[
                {
                    "heading": heading,
                    "body": edited_body if "旅游实用价值" in instructions else draft_body,
                    "author_agent_ids": ["history_culture"],
                    "evidence_ids": [usable],
                }
            ],
        )

    async def run():
        async def retrieve(_):
            return [evidence]

        monkeypatch.setattr(editorial, "finalize_revision", check_revision)
        state = await build_research_graph(model, retrieve=retrieve, skills=registry).ainvoke(
            research_input()
        )
        assert state["status"] == "completed"
        assert state["review"].decision == "approved"
        assert state["review_round"] == (2 if revise else 1)
        assert state["draft"].sections[0].evidence_ids == [evidence.evidence_id]
        assert state["draft"].sections[0].author_agent_ids == ["history_culture"]

    asyncio.run(run())
    round_calls = [
        ("writer", "ReportOutline"),
        ("writer", "EvidenceSynthesis"),
        ("writer", "ReportDraft"),
        ("writer", "ReportDraft"),
        ("reviewer", "Review"),
    ]
    assert recorder == round_calls + (round_calls[:-1] if revise else [])
    assert len(checked_revisions) == int(revise)


def test_outline_and_synthesis_preserve_known_gaps_and_source_boundaries():
    evidence = source()
    existing = Finding(
        statement="关于遗产范围的口径不同",
        support="disputed",
        evidence_ids=[evidence.evidence_id],
    )
    contributions = [
        ResearchResult(
            kind="research",
            run_id=uuid4(),
            agent_id=role,
            findings=[
                existing,
                Finding(statement="预约条件待核", support="insufficient"),
            ]
            if role == "history_culture"
            else [existing],
        )
        for role in ("history_culture", "tourism_resources")
    ]
    refs, _ = catalog(contributions, [evidence])
    assert len(refs) == 2
    assert refs[1].agent_ids == ["history_culture", "tourism_resources"]
    without_usable_evidence, _ = catalog(contributions, [])
    assert without_usable_evidence[1].support == "disputed"
    assert not without_usable_evidence[1].evidence_ids
    outline = ReportOutline(
        title="泉州",
        sections=[{"heading": "遗产游览", "purpose": "说明看点", "finding_ids": [1, 2]}],
    )
    require_outline(outline, refs)
    with pytest.raises(InvalidOutput, match="exactly once"):
        require_outline(outline.model_copy(update={"omitted_finding_ids": [2]}), refs)

    synthesis = EvidenceSynthesis(
        sections=[
            {
                "heading": "遗产游览",
                "points": [{"finding_ids": [1, 2], "summary": "核验游览条件"}],
                "gaps": ["预约仍需核对"],
            }
        ]
    )
    with pytest.raises(InvalidOutput, match="known conflict"):
        require_synthesis(synthesis, outline, refs)
    synthesis.sections[0].conflicts = ["遗产范围口径有差异"]
    require_synthesis(synthesis, outline, refs)

    draft = ReportDraft(
        title="泉州",
        sections=[
            {
                "heading": "遗产游览",
                "body": "预约仍需核实。",
                "author_agent_ids": ["writer"],
                "evidence_ids": [evidence.evidence_id],
            }
        ],
    )
    with pytest.raises(InvalidOutput, match="unmapped contributor"):
        require_draft(draft, outline, refs)
    draft.sections[0].author_agent_ids = ["tourism_resources"]
    require_draft(draft, outline, refs)
    edited = draft.model_copy(deep=True)
    edited.sections[0].author_agent_ids = ["history_culture"]
    with pytest.raises(InvalidOutput, match="retain section attribution"):
        require_draft(edited, outline, refs, original=draft)
