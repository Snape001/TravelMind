from uuid import UUID

from travelmind.research.social.skills import SOCIAL_SCOPE, with_social_sample_notes
from travelmind.runtime.execution import InvalidOutput, result_identity, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.skills import SkillRegistry
from travelmind.runtime.skills.contracts import ReviewInput
from travelmind.runtime.skills.research import compact_evidence, compact_findings
from travelmind.runtime.skills.writer import write_report
from travelmind.schemas.agents import AgentInput, ReportResult, ResearchResult, ReviewResult
from travelmind.schemas.domain import ReportDraft, ReportSection, Review, ReviewIssue
from travelmind.schemas.research_quality import ExpertResearchResult

WRITER = "writer"
REVIEWER = "reviewer"


def usable_evidence(experts: list[ExpertResearchResult]) -> set[UUID]:
    return {
        card.evidence_id for expert in experts for card in expert.quality if card.level != "low"
    }


def eligible_evidence(
    available: set[UUID],
    experts: list[ExpertResearchResult] | None,
    verified_map: frozenset[UUID],
) -> set[UUID]:
    return available & (usable_evidence(experts) | verified_map) if experts else available


def finalize_revision(
    request: AgentInput,
    contributor_ids: set[str],
    *,
    experts: list[ExpertResearchResult],
    verified_map: frozenset[UUID],
) -> ReportDraft:
    assert request.draft is not None
    eligible = eligible_evidence(
        {item.evidence_id for item in request.evidence}, experts, verified_map
    )
    sections = [
        section
        for section in request.draft.sections
        if set(section.evidence_ids) <= eligible
        and set(section.author_agent_ids) <= contributor_ids
    ]
    if len(sections) == len(request.draft.sections):
        return request.draft
    sections.append(
        ReportSection(
            heading="资料待核实",
            body="部分章节的引用或专家署名未通过校验，相关结论暂不发布。",
            author_agent_ids=[WRITER],
        )
    )
    return request.draft.model_copy(update={"sections": sections})


def editorial_assessments(experts: list[ExpertResearchResult]) -> list[dict]:
    return [
        {
            "agent_id": expert.research.agent_id,
            "unresolved_questions": expert.unresolved_questions,
            "reflection_state": expert.reflection.state,
            "reflection_reason": expert.reflection.reason,
            "quality_levels": {str(card.evidence_id): card.level for card in expert.quality},
        }
        for expert in experts
    ]


async def write(
    model: StructuredModel,
    request: AgentInput,
    contributions: list[ResearchResult],
    previous_review: Review | None,
    *,
    experts: list[ExpertResearchResult] | None = None,
    verified_map: frozenset[UUID] = frozenset(),
    skills: SkillRegistry | None = None,
) -> ReportResult:
    if skills is not None:
        if experts:
            eligible = usable_evidence(experts) | verified_map
            request = request.model_copy(
                update={
                    "evidence": [item for item in request.evidence if item.evidence_id in eligible]
                }
            )
        report = await write_report(
            skills, model, request, contributions, previous_review, experts or []
        )
        return ReportResult(**result_identity(request), kind="report", report=report)

    findings = [
        {
            "agent_id": result.agent_id,
            "findings": [f.model_dump(mode="json") for f in result.findings],
        }
        for result in contributions
    ]
    author_ids = "、".join(sorted({result.agent_id for result in contributions}))
    structure = (
        "根据用户主题决定章节数量。范围很窄时只用一到两节，先直接回答用户的问题。"
        if experts
        else "将相关主题合并成 3–6 节，每节正文控制在约 400 字以内。"
    )
    instructions = (
        "担任首席撰写师。按用户主题编排简体中文报告。"
        "各章节提供标题、正文、专家署名和证据 ID。"
        f"{structure}"
        "事实必须有输入证据支持。说明共识、分歧和缺口，并将待核实信息明确标记为待核实。"
        "不得把问题或推测写成事实。"
        "根据 contributions 汇总各专家成果。"
        f"author_agent_ids 只能使用以下专家 ID：{author_ids}。"
        "撰写师、总监、审核员及通用角色名称不得署名。"
        "综合说明若无对应研究贡献，请并入有对应贡献的正文；"
        "每节逐条核对引用是否支持对应事实，删去材料无法支持的数字和细节。"
    )
    if previous_review is not None:
        instructions += (
            "\n修订输入中的 draft，逐条处理 previous_review 中的审核意见。若材料不足以回答，"
            "在对应章节标注缺少的具体材料和待核实事项。"
        )
    data = {
        "contributions": findings,
        "previous_review": previous_review.model_dump(mode="json") if previous_review else None,
    }
    if experts:
        eligible = usable_evidence(experts) | verified_map
        request = request.model_copy(
            update={"evidence": [item for item in request.evidence if item.evidence_id in eligible]}
        )
        instructions += (
            "只处理用户明确的调研范围。每条重要事实使用能直接支撑它的一个或少量来源，"
            "不重复堆放相近转述，不铺陈用户未问的列表、数字或假设。"
            "用 expert_assessments 提示直接影响答案的待核事项及不同口径。"
            "只能引用 input.evidence 中的 ID。证据不足可直接交代缺口，不能将其写成断言。"
        )
        data["expert_assessments"] = editorial_assessments(experts)
    if any(item.source_type == "social" for item in request.evidence):
        instructions += SOCIAL_SCOPE
    report = await with_budget(
        lambda: model(request, instructions, ReportDraft, context=data), request.budget
    )
    report = with_social_sample_notes(report, request.evidence)
    return ReportResult(**result_identity(request), kind="report", report=report)


async def review(
    model: StructuredModel,
    request: AgentInput,
    contributor_ids: set[str],
    *,
    experts: list[ExpertResearchResult] | None = None,
    verified_map: frozenset[UUID] = frozenset(),
    skills: SkillRegistry | None = None,
) -> ReviewResult:
    assert request.draft is not None
    evidence_ids = {item.evidence_id for item in request.evidence}
    eligible = eligible_evidence(evidence_ids, experts, verified_map)
    issues = []
    for section in request.draft.sections:
        if set(section.evidence_ids) - evidence_ids:
            issues.append(
                ReviewIssue(category="citation", message=f"核对「{section.heading}」中的未知引用。")
            )
        unusable = [
            item
            for item in dict.fromkeys(section.evidence_ids)
            if item in evidence_ids and item not in eligible
        ]
        if unusable:
            issues.append(
                ReviewIssue(
                    category="citation",
                    message=f"「{section.heading}」引用的资料未达到可用等级，请替换或保留核验缺口。",
                    evidence_ids=unusable,
                )
            )
        if set(section.author_agent_ids) - contributor_ids:
            issues.append(
                ReviewIssue(
                    category="consistency", message=f"更正「{section.heading}」中的专家署名。"
                )
            )

    def validate_verdict(verdict: Review) -> None:
        if any(set(issue.evidence_ids) - evidence_ids for issue in verdict.issues):
            raise InvalidOutput("review cites unknown evidence")
        if verdict.decision == "approved" and verdict.issues:
            raise InvalidOutput("approval cannot have unresolved issues")

    async def audit() -> Review:
        instructions = (
            "担任独立审核员。阅读 draft 的各章节。"
            "逐条核对关键事实是否得到所引 evidence 原文支持。"
            "审查主题覆盖、专家分歧和证据不足时的表述。报告准确标注的资料缺口可以保留。"
            "仅在所有检查通过时返回 approved 和空 issues；"
            "否则返回 revision_required 和针对具体章节、可执行的 issues。"
            "issues 只引用输入的证据 ID。"
        )
        if any(item.source_type == "social" for item in request.evidence):
            instructions += SOCIAL_SCOPE
        data = None
        if experts:
            data = {"expert_assessments": editorial_assessments(experts)}
            instructions += (
                "只审核用户任务范围内实质性的覆盖与证据缺口，"
                "按 expert_assessments 检查与用户答案直接相关的争议。"
                "对时效敏感的结论核对日期，评级不能代替同章摘录支撑。"
            )
        verdict = await model(request, instructions, Review, context=data)
        validate_verdict(verdict)
        return verdict

    if issues:
        verdict = Review(decision="revision_required", issues=issues)
    elif skills is not None:
        cited = {ref for section in request.draft.sections for ref in section.evidence_ids}
        evidence = sorted(request.evidence, key=lambda item: item.evidence_id not in cited)
        projection = request.model_copy(
            update={
                "evidence": compact_evidence(evidence[:40]),
                "prior_findings": compact_findings(request.prior_findings[:24]),
            }
        )
        session = skills.session(projection, "review")
        verdict = await session.run(
            model,
            ReviewInput(
                contributor_ids=sorted(contributor_ids),
                expert_assessments=editorial_assessments(experts or []),
            ),
            Review,
            validate=validate_verdict,
        )
    else:
        verdict = await with_budget(audit, request.budget)
    return ReviewResult(**result_identity(request), kind="review", review=verdict)
