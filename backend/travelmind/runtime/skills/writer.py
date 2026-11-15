from itertools import zip_longest
from unicodedata import normalize
from uuid import UUID

from travelmind.runtime.execution import InvalidOutput, RunError
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.skills.contracts import (
    CatalogFinding,
    EvidenceSynthesis,
    OutlineInput,
    ReportOutline,
    SynthesisInput,
    WritingInput,
)
from travelmind.runtime.skills.registry import SkillRegistry
from travelmind.runtime.skills.research import compact_evidence
from travelmind.schemas.agents import AgentInput, ResearchResult
from travelmind.schemas.domain import Evidence, ReportDraft, Review
from travelmind.schemas.research_quality import ExpertResearchResult


def catalog(
    contributions: list[ResearchResult], evidence: list[Evidence]
) -> tuple[dict[int, CatalogFinding], list[Evidence]]:
    available = {source.evidence_id: source for source in evidence}
    selected: dict[UUID, Evidence] = {}
    entries: dict[tuple, CatalogFinding] = {}
    # Give each contributing expert a first pass before taking their later findings.
    candidates = zip_longest(
        *([(result.agent_id, f) for f in result.findings] for result in contributions)
    )
    for candidate in candidates:
        for entry in candidate:
            if entry is None:
                continue
            agent_id, finding = entry
            ids = []
            for ref in dict.fromkeys(finding.evidence_ids):
                if ref in available and (ref in selected or len(selected) < 32):
                    selected[ref] = available[ref]
                    ids.append(ref)
                if len(ids) == 6:
                    break
            support = (
                "insufficient" if not ids and finding.support == "supported" else finding.support
            )
            key = (finding.statement, support, frozenset(ids))
            if key not in entries and len(entries) < 48:
                entries[key] = CatalogFinding(
                    id=len(entries) + 1,
                    agent_ids=[agent_id],
                    statement=finding.statement[:600],
                    evidence_ids=ids,
                    support=support,
                )
            elif key in entries and agent_id not in entries[key].agent_ids:
                entries[key].agent_ids.append(agent_id)
    return {entry.id: entry for entry in entries.values()}, list(selected.values())


def headings(report: ReportOutline | EvidenceSynthesis | ReportDraft) -> list[str]:
    return [section.heading for section in report.sections]


def require_outline(outline: ReportOutline, refs: dict[int, CatalogFinding]) -> None:
    keys = {"".join(normalize("NFKC", heading).casefold().split()) for heading in headings(outline)}
    if len(keys) != len(outline.sections):
        raise InvalidOutput("outline must contain distinct headings")
    assigned = [ref for section in outline.sections for ref in section.finding_ids]
    assigned.extend(outline.omitted_finding_ids)
    if len(assigned) != len(refs) or set(assigned) != refs.keys():
        raise InvalidOutput("outline must assign or omit each known finding exactly once")


def require_synthesis(
    synthesis: EvidenceSynthesis, outline: ReportOutline, refs: dict[int, CatalogFinding]
) -> None:
    if headings(synthesis) != headings(outline):
        raise InvalidOutput("synthesis must follow the outline")
    for section, plan in zip(synthesis.sections, outline.sections, strict=True):
        used = [ref for point in section.points for ref in point.finding_ids]
        if len(used) != len(plan.finding_ids) or set(used) != set(plan.finding_ids):
            raise InvalidOutput("synthesis must use each section finding exactly once")
        states = {refs[ref].support for ref in used}
        if "disputed" in states and not section.conflicts:
            raise InvalidOutput("synthesis lost a known conflict")
        if (
            "insufficient" in states or any(not refs[ref].evidence_ids for ref in used)
        ) and not section.gaps:
            raise InvalidOutput("synthesis lost a known evidence gap")


def require_draft(
    draft: ReportDraft,
    outline: ReportOutline,
    refs: dict[int, CatalogFinding],
    *,
    original: ReportDraft | None = None,
) -> None:
    if draft.title != outline.title or headings(draft) != headings(outline):
        raise InvalidOutput("report must preserve the approved outline and headings")
    for index, (section, plan) in enumerate(zip(draft.sections, outline.sections, strict=True)):
        authors = set(section.author_agent_ids)
        citations = set(section.evidence_ids)
        if len(authors) != len(section.author_agent_ids) or len(citations) != len(
            section.evidence_ids
        ):
            raise InvalidOutput("section authors and citations must be unique")
        allowed_authors = {agent for ref in plan.finding_ids for agent in refs[ref].agent_ids}
        allowed_ids = {item for ref in plan.finding_ids for item in refs[ref].evidence_ids}
        if authors - allowed_authors or citations - allowed_ids:
            raise InvalidOutput("section includes an unmapped contributor or source")
        if allowed_ids and not citations:
            raise InvalidOutput("section must retain supporting citations")
        if original is not None:
            before = original.sections[index]
            if authors != set(before.author_agent_ids) or citations != set(before.evidence_ids):
                raise InvalidOutput("global edit must retain section attribution and citations")


async def write_report(
    registry: SkillRegistry,
    model: StructuredModel,
    request: AgentInput,
    contributions: list[ResearchResult],
    previous_review: Review | None,
    experts: list[ExpertResearchResult],
) -> ReportDraft:
    refs, evidence = catalog(contributions, request.evidence)
    if not refs:
        raise RunError("NO_RESEARCH_RESULTS", "缺少可编入报告的专家成果")

    def stage(name: str, **updates):
        projected = request.model_copy(
            update={"evidence": [], "prior_findings": [], "draft": None} | updates
        )
        return registry.session(projected, name)

    questions = dict.fromkeys(
        text[:200] for expert in experts for text in expert.unresolved_questions
    )
    notes = [item.message[:500] for item in previous_review.issues[:12]] if previous_review else []
    material = OutlineInput(
        findings=list(refs.values()),
        unresolved_questions=list(questions)[:24],
        revision_notes=notes,
    )
    outline = await stage("outline").run(
        model, material, ReportOutline, validate=lambda value: require_outline(value, refs)
    )
    planned = SynthesisInput(**material.model_dump(), outline=outline)
    synthesis = await stage("synthesize", evidence=compact_evidence(evidence, max_chars=1000)).run(
        model,
        planned,
        EvidenceSynthesis,
        validate=lambda value: require_synthesis(value, outline, refs),
    )
    compiled = WritingInput(**planned.model_dump(), synthesis=synthesis)
    excerpts = compact_evidence(evidence, max_chars=750)
    draft = await stage("write", draft=request.draft, evidence=excerpts).run(
        model, compiled, ReportDraft, validate=lambda value: require_draft(value, outline, refs)
    )
    return await stage("edit", draft=draft, evidence=excerpts).run(
        model,
        compiled,
        ReportDraft,
        validate=lambda value: require_draft(value, outline, refs, original=draft),
    )
