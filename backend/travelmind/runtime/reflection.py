from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, TypeAdapter

from travelmind.research.quality_rules import distinct_origins
from travelmind.runtime.execution import InvalidOutput, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import Contract, Evidence, Finding, ShortText
from travelmind.schemas.research_quality import (
    FOLLOW_UP,
    EvidenceQuality,
    ReflectionAction,
    ReflectionState,
    ReflectionStopReason,
)

STATE_ORDER: tuple[ReflectionState, ...] = ("conflicting", "low_quality", "insufficient")
AGE_SENSITIVE = ("开放时间", "营业时间", "票价", "预约", "交通", "时刻表", "限流", "住宿", "天气")
QUALITY_BATCH_SIZE = 4
MAX_QUALITY_BATCHES = 2
ROUND_MODEL_OPERATIONS = 2 + MAX_QUALITY_BATCHES  # findings, assessment and quality batches


def round_key(run_id: UUID, number: int) -> str:
    return str(uuid5(run_id, f"reflection:{number}"))


class QualityScorer(Protocol):
    """Score one batch in a single semantic call. The caller owns retries."""

    async def __call__(
        self, request: AgentInput, *, as_of: datetime, max_age_days: int | None
    ) -> Sequence[EvidenceQuality]: ...


class EvidenceRanker(Protocol):
    async def __call__(
        self, request: AgentInput, *, as_of: datetime, max_age_days: int | None
    ) -> Sequence[Evidence]: ...


@dataclass(frozen=True)
class ReflectionConfig:
    model_id: str
    score: QualityScorer | None = None
    max_follow_up_searches: int = 2
    max_output_tokens: int = 57_344
    rank: EvidenceRanker | None = None

    def __post_init__(self):
        TypeAdapter(ShortText).validate_python(self.model_id)
        if not 0 <= self.max_follow_up_searches <= 2 or self.max_output_tokens < 0:
            raise ValueError("invalid reflection budget")


class QuestionAssessment(Contract):
    question: ShortText
    reason: ShortText
    finding_index: int | None = Field(default=None, ge=0)
    related_ids: list[UUID] = Field(default_factory=list, max_length=16)
    original_id: UUID | None = None
    corroborating_ids: list[UUID] = Field(default_factory=list, max_length=2)
    conflict_ids: list[UUID] = Field(default_factory=list, max_length=2)


class Assessment(Contract):
    questions: list[QuestionAssessment] = Field(min_length=1, max_length=5)


class QuestionResult(Contract):
    question: ShortText
    reason: ShortText
    state: ReflectionState
    finding: Finding


class RoundResult(Contract):
    round: int
    as_of: AwareDatetime
    max_age_days: int | None
    evidence: list[Evidence]
    quality: list[EvidenceQuality]
    questions: list[QuestionResult]
    queries: list[str]
    budget: RunBudget
    reserved_tokens: int
    model_id: ShortText | None = None
    new_sources: bool = False
    halted: Literal["budget_exhausted", "assessment_unavailable"] | None = None


class RoundWork(Contract):
    request: AgentInput
    as_of: AwareDatetime
    max_age_days: int | None
    reserved_tokens: int
    previous: RoundResult | None = None
    action: ReflectionAction | None = None
    search_focus: str = ""
    query_key: str = ""
    peer_ids: list[str] = Field(default_factory=list, max_length=2)


async def score_batch(
    scorer: QualityScorer,
    request: AgentInput,
    *,
    as_of: datetime,
    max_age_days: int | None,
) -> list[EvidenceQuality]:
    expected = {item.evidence_id: item.excerpt for item in request.evidence}
    assert request.assignment

    async def rate() -> list[EvidenceQuality]:
        values = await scorer(request, as_of=as_of, max_age_days=max_age_days)
        cards = TypeAdapter(list[EvidenceQuality]).validate_python(values)
        if not cards:
            raise InvalidOutput("no evidence quality assessment was available for the batch")
        seen = set()
        for card in cards:
            if (
                card.evidence_id not in expected
                or card.evidence_id in seen
                or card.assignment_id != request.assignment.assignment_id
                or (card.as_of, card.max_age_days) != (as_of, max_age_days)
            ):
                raise InvalidOutput("quality card does not match the evidence batch")
            excerpt = expected[card.evidence_id]
            if any(
                part.quote is not None and part.quote not in excerpt
                for part in (card.relevance, card.directness)
            ):
                raise InvalidOutput("quality card quotes text outside the evidence excerpt")
            seen.add(card.evidence_id)
        return cards

    return await with_budget(rate, request.budget)


def independent_pair(ids: list[UUID], evidence: dict[UUID, Evidence]) -> bool:
    if len(ids) != 2 or not all(item in evidence for item in ids):
        return False
    left, right = (evidence[item] for item in ids)
    return distinct_origins(left, right)


def judge_question(
    question: QuestionAssessment,
    candidates: list[Finding],
    evidence: dict[UUID, Evidence],
    quality: dict[UUID, EvidenceQuality],
) -> QuestionResult:
    original = [question.original_id] if question.original_id is not None else []
    related = list(
        dict.fromkeys(
            question.related_ids + original + question.corroborating_ids + question.conflict_ids
        )
    )
    if set(related) - evidence.keys():
        raise InvalidOutput("reflection cites unknown evidence")
    index = question.finding_index
    if index is not None and index >= len(candidates):
        raise InvalidOutput("reflection cites an unknown finding")
    usable = {
        key: evidence[key] for key in related if key in quality and quality[key].level != "low"
    }
    if independent_pair(question.conflict_ids, usable):
        state, support, ids = "conflicting", "disputed", question.conflict_ids
    else:
        basis = []
        if question.original_id in usable and quality[question.original_id].directness.score == 2:
            basis = original
        elif independent_pair(question.corroborating_ids, usable):
            basis = question.corroborating_ids
        candidate = candidates[index] if index is not None else None
        if (
            candidate
            and candidate.support == "supported"
            and set(basis) & set(candidate.evidence_ids)
        ):
            state, support, ids = "sufficient", "supported", basis
        else:
            state, support, ids = "insufficient", "insufficient", related
            rated = [quality[key] for key in related if key in quality]
            relevant = [card for card in rated if card.relevance.score > 0]
            if (
                len(rated) == len(related)
                and relevant
                and all(card.level == "low" for card in relevant)
            ):
                state = "low_quality"
    statement = (
        candidates[index].statement
        if support == "supported" and index is not None
        else (
            f"存在争议：{question.question}"
            if support == "disputed"
            else f"待核实：{question.question}"
        )
    )
    return QuestionResult(
        question=question.question,
        reason=question.reason,
        state=state,
        finding=Finding(statement=statement, support=support, evidence_ids=ids),
    )


async def assess(
    model: StructuredModel,
    request: AgentInput,
    candidates: list[Finding],
    cards: list[EvidenceQuality],
    questions: list[str],
    peer_findings: list[Finding],
) -> list[QuestionResult]:
    evidence = {item.evidence_id: item for item in request.evidence}
    quality = {card.evidence_id: card for card in cards}
    context = {
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "quality": [item.model_dump(mode="json") for item in cards],
        "required_questions": questions,
        "peer_findings": [item.model_dump(mode="json") for item in peer_findings],
    }

    async def evaluate() -> list[QuestionResult]:
        result = await model(
            request,
            "作为本分工专家进行局部反思。仅核对本分工目标中的关键问题。首轮列出所有关键问题"
            "（最多五个），只保留影响用户所问内容的具体问题。"
            "有 required_questions 时逐字沿用，每题恰好判断一次。每个问题给出简短"
            "依据、相关证据 ID，以及对应的候选结论序号（从 0 开始，找不到填 null）。"
            "逐条核读摘录。只有原始发布直接支撑结论时才填 original_id；"
            "只有两份来源独立且均能支撑同一结论时才填 corroborating_ids。"
            "只有两条独立证据在同一时间和适用条件下有明确相反说法才填 conflict_ids，"
            "各选一侧，并在理由解释矛盾。不同时间、范围或条件应拆开核对。"
            "转载、互引同一发布和来源关系不明均不能计作独立来源。"
            "证据或判断不充分时留空相应字段；质询意见仅是核验线索。",
            Assessment,
            context=context,
        )
        names = [item.question for item in result.questions]
        if len(set(names)) != len(names) or questions and set(names) != set(questions):
            raise InvalidOutput("reflection must assess each saved question exactly once")
        by_name = {
            item.question: judge_question(item, candidates, evidence, quality)
            for item in result.questions
        }
        return [by_name[name] for name in questions or names]

    return await with_budget(evaluate, request.budget)


def unresolved(question: str, reason: str) -> QuestionResult:
    return QuestionResult(
        question=question,
        reason=reason,
        state="insufficient",
        finding=Finding(statement=f"待核实：{question}", support="insufficient"),
    )


def decide(
    result: RoundResult,
    *,
    max_rounds: int,
    budget_exhausted: bool,
    duplicate_query: bool = False,
) -> ReflectionAction:
    state = next(
        (level for level in STATE_ORDER if any(item.state == level for item in result.questions)),
        "sufficient",
    )
    targets = [item for item in result.questions if item.state == state]
    stop: ReflectionStopReason | None = None
    reason = "；".join(item.reason for item in targets)
    if result.halted:
        state, stop = "insufficient", result.halted
        reason = (
            "局部反思未获得有效判断。"
            if stop == "assessment_unavailable"
            else "局部反思预算已耗尽。"
        )
    elif state == "sufficient":
        stop = "sufficient"
    elif budget_exhausted:
        stop, reason = "budget_exhausted", "追加检索或模型预算已耗尽。"
    elif result.round and not result.new_sources:
        stop, reason = "no_new_evidence", "补查未增加独立来源。"
    elif result.round == max_rounds:
        stop = "round_limit"
    elif duplicate_query:
        stop, reason = "budget_exhausted", "剩余问题没有尚未执行的定向检索。"

    ids = list(dict.fromkeys(key for item in targets for key in item.finding.evidence_ids))
    return ReflectionAction(
        state=state,
        strategy="accept" if stop == "sufficient" else "stop" if stop else FOLLOW_UP[state],
        round=result.round,
        max_rounds=max_rounds,
        stop_reason=stop,
        reason=reason,
        target_questions=[] if stop else [item.question for item in targets],
        model_id=result.model_id,
        evidence_ids=ids,
    )
