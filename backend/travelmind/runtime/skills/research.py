from travelmind.runtime.execution import InvalidOutput, RunError
from travelmind.runtime.experts import EvidenceRetriever, Findings, retrieve_evidence
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.planning import DirectorDecision
from travelmind.runtime.skills.contracts import DirectorInput, ResearchInput
from travelmind.runtime.skills.registry import SkillRegistry
from travelmind.schemas.agents import AgentInput, ResearchResult
from travelmind.schemas.domain import Evidence, Finding


def compact_evidence(evidence: list[Evidence], *, max_chars: int = 1600) -> list[Evidence]:
    return [
        source.model_copy(
            update={
                "excerpt": source.excerpt[:max_chars],
                "source_title": source.source_title[:300],
                "source_locator": source.source_locator[:350] if source.source_locator else None,
            }
        )
        for source in evidence
    ]


def compact_findings(findings: list[Finding]) -> list[Finding]:
    return [
        item.model_copy(
            update={"statement": item.statement[:600], "evidence_ids": item.evidence_ids[:6]}
        )
        for item in findings
    ]


async def director_skill(
    registry: SkillRegistry,
    model: StructuredModel,
    request: AgentInput,
    clarification_answers: list[dict[str, str]] | None = None,
) -> DirectorDecision:
    if request.agent_id != "research_director" or request.assignment or request.draft:
        raise RunError("SKILL_NOT_AVAILABLE", "需求梳理需要总监与当前调研需求")
    session = registry.session(request, "research")
    return await session.run(
        model, DirectorInput(clarification_answers=clarification_answers or []), DirectorDecision
    )


async def research_skill(
    registry: SkillRegistry,
    model: StructuredModel,
    request: AgentInput,
    *,
    retrieve: EvidenceRetriever | None = None,
    focus: list[str] | None = None,
) -> ResearchResult:
    if not request.assignment or request.assignment.agent_id != request.agent_id:
        raise RunError("SKILL_NOT_AVAILABLE", "专业技能需要当前专家的有效分工")
    stage = "plan_route" if request.agent_id == "travel_planning" else "research"
    subject = ResearchInput(round_focus=focus or [])
    session = registry.session(request, stage)
    sources = {item.evidence_id: item for item in request.evidence}
    known = set(sources)
    added = {}
    if retrieve is not None:
        session.take_tool("retrieve_evidence", {"retrieve_evidence"})
        tool_budget = session.request.budget.model_copy(update={"max_attempts": 1})
        tool_request = request.model_copy(update={"budget": tool_budget})
        for evidence in await retrieve_evidence(retrieve, tool_request):
            if sources.setdefault(evidence.evidence_id, evidence) != evidence:
                raise RunError("EVIDENCE_CONFLICT", "同一证据 ID 对应不同资料")
            if evidence.evidence_id not in known:
                added[evidence.evidence_id] = evidence
    # The report and storage keep the original evidence; only the model projection is bounded.
    evidence = compact_evidence(list((added | sources).values())[:14], max_chars=1000)
    source_ids = {item.evidence_id for item in evidence}
    session.request = session.request.model_copy(
        update={
            "evidence": evidence,
            "prior_findings": compact_findings(request.prior_findings[:12]),
        }
    )

    def check(findings: Findings) -> None:
        if any(set(item.evidence_ids) - source_ids for item in findings.findings):
            raise InvalidOutput("finding cites unknown evidence")

    result = await session.run(model, subject, Findings, validate=check)
    return ResearchResult(
        run_id=request.run_id,
        agent_id=request.agent_id,
        kind="research",
        findings=result.findings,
        evidence=list(added.values()),
    )
