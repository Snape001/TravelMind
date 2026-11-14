from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from pydantic import Field

from travelmind.runtime.execution import InvalidOutput, RunError, emit, result_identity, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.planning import EXPERTS
from travelmind.schemas.agents import AgentInput, ResearchResult
from travelmind.schemas.domain import Contract, Evidence, Finding, Text
from travelmind.schemas.events import ToolFinishedEvent, ToolStartedEvent

EvidenceRetriever = Callable[[AgentInput], Awaitable[list[Evidence]]]


class Findings(Contract):
    findings: list[Finding] = Field(min_length=1, max_length=10)


class Challenge(Contract):
    finding_index: int = Field(ge=0)
    question: Text = Field(max_length=600)


async def retrieve_evidence(retrieve: EvidenceRetriever, request: AgentInput) -> list[Evidence]:
    tool = {**result_identity(request), "tool_call_id": uuid4()}
    emit(ToolStartedEvent(type="tool.started", **tool, tool_name="retrieve_evidence"))
    started = perf_counter()
    try:
        sources = await with_budget(lambda: retrieve(request), request.budget)
    except RunError as exc:
        emit(
            ToolFinishedEvent(
                type="tool.finished",
                **tool,
                status="failed",
                error=exc.info,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        )
        raise
    emit(
        ToolFinishedEvent(
            type="tool.finished",
            **tool,
            status="completed",
            duration_ms=int((perf_counter() - started) * 1000),
        )
    )
    return sources


async def research(
    model: StructuredModel, retrieve: EvidenceRetriever | None, request: AgentInput
) -> ResearchResult:
    known = {item.evidence_id: item for item in request.evidence}
    retrieved = await retrieve_evidence(retrieve, request) if retrieve else []
    sources = dict(known)
    for item in retrieved:
        if sources.setdefault(item.evidence_id, item) != item:
            raise RunError("EVIDENCE_CONFLICT", "同一证据 ID 对应不同资料")
    findings = await find(
        model,
        request.model_copy(update={"evidence": list(sources.values())}),
        f"{EXPERTS[request.agent_id]} 根据 assignment 处理本次研究任务。"
        "区分资料可支持的结论、存在争议的结论和资料缺口。"
        "如果没有证据，输出待核实的问题，使用 insufficient。",
    )
    return ResearchResult(
        **result_identity(request),
        kind="research",
        findings=findings.findings,
        evidence=[item for evidence_id, item in sources.items() if evidence_id not in known],
    )


async def find(
    model: StructuredModel,
    request: AgentInput,
    instructions: str,
    *,
    context: dict | None = None,
) -> Findings:
    allowed = {item.evidence_id for item in request.evidence}

    async def make_findings() -> Findings:
        findings = await model(
            request,
            f"{instructions}\n只使用输入 evidence 中的 ID，且所引原文须支持结论。"
            "每条结论明确标记 supported、disputed 或 insufficient。",
            Findings,
            context=context,
        )
        if any(set(finding.evidence_ids) - allowed for finding in findings.findings):
            raise InvalidOutput("finding cites unknown evidence")
        return findings

    return await with_budget(make_findings, request.budget)


async def challenge(model: StructuredModel, request: AgentInput, target: str) -> Challenge:
    async def ask() -> Challenge:
        question = await model(
            request,
            f"{EXPERTS[request.agent_id]} 向 {target} 提出一条交叉质询。针对 prior_findings 中"
            "一个具体结论，填写从 0 开始的 finding_index。核对证据是否匹配、存在矛盾或有适用限制；"
            "缺少资料时问清核验缺口。",
            Challenge,
        )
        if question.finding_index >= len(request.prior_findings):
            raise InvalidOutput("challenge targets an unknown finding")
        return question

    return await with_budget(ask, request.budget)


async def answer(
    model: StructuredModel, request: AgentInput, question: Challenge
) -> ResearchResult:
    findings = await find(
        model,
        request,
        f"{EXPERTS[request.agent_id]} 答复质询，针对被质疑的结论明确维持、修正或标注局限。"
        "question 字段记录对方提出的质询。",
        context={"question": question.question},
    )
    return ResearchResult(**result_identity(request), kind="research", findings=findings.findings)
