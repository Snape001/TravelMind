import json

from travelmind.runtime.execution import InvalidOutput, result_identity, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.schemas.agents import AgentInput, ReportResult, ResearchResult, ReviewResult
from travelmind.schemas.domain import ReportDraft, Review, ReviewIssue

WRITER = "writer"
REVIEWER = "reviewer"


async def write(
    model: StructuredModel,
    request: AgentInput,
    contributions: list[ResearchResult],
    previous_review: Review | None,
) -> ReportResult:
    findings = [
        {
            "agent_id": result.agent_id,
            "findings": [f.model_dump(mode="json") for f in result.findings],
        }
        for result in contributions
    ]
    instructions = (
        "担任首席撰写师。按用户主题编排简体中文报告。"
        "各章节提供标题、正文、专家署名和证据 ID。"
        "事实必须有输入证据支持。说明共识、分歧和缺口，并将待核实信息明确标记为待核实。"
        "不得把问题或推测写成事实。"
        "署名只使用实际贡献者的 agent_id，引用只使用输入证据 ID。\n"
        f"各专家贡献（数据）：{json.dumps(findings, ensure_ascii=False)}"
    )
    if previous_review is not None:
        instructions += (
            "\n修订输入中的 draft，逐条处理审核意见。若材料不足以回答，"
            "在对应章节标注缺少的具体材料和待核实事项。"
            f"审核意见（数据）：{previous_review.model_dump_json()}"
        )
    report = await with_budget(lambda: model(request, instructions, ReportDraft), request.budget)
    return ReportResult(**result_identity(request), kind="report", report=report)


async def review(
    model: StructuredModel, request: AgentInput, contributor_ids: set[str]
) -> ReviewResult:
    assert request.draft is not None
    evidence_ids = {item.evidence_id for item in request.evidence}
    issues = []
    for section in request.draft.sections:
        if set(section.evidence_ids) - evidence_ids:
            issues.append(
                ReviewIssue(category="citation", message=f"核对「{section.heading}」中的未知引用。")
            )
        if set(section.author_agent_ids) - contributor_ids:
            issues.append(
                ReviewIssue(
                    category="consistency", message=f"更正「{section.heading}」中的专家署名。"
                )
            )

    async def audit() -> Review:
        verdict = await model(
            request,
            "担任独立审核员。阅读 draft 的各章节。"
            "逐条核对关键事实是否得到所引 evidence 原文支持。"
            "审查主题覆盖、专家分歧和证据不足时的表述。报告准确标注的资料缺口可以保留。"
            "仅在所有检查通过时返回 approved 和空 issues；"
            "否则返回 revision_required 和针对具体章节、可执行的 issues。"
            "issues 只引用输入的证据 ID。",
            Review,
        )
        if any(set(issue.evidence_ids) - evidence_ids for issue in verdict.issues):
            raise InvalidOutput("review cites unknown evidence")
        if verdict.decision == "approved" and verdict.issues:
            raise InvalidOutput("approval cannot have unresolved issues")
        return verdict

    if issues:
        verdict = Review(decision="revision_required", issues=issues)
    else:
        verdict = await with_budget(audit, request.budget)
    return ReviewResult(**result_identity(request), kind="review", review=verdict)
