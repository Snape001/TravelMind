from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, NamedTuple
from uuid import UUID

from travelmind.research.quality_rules import (
    body_text,
    completeness,
    freshness,
    independence,
    provenance,
    relevance,
    terms,
)
from travelmind.runtime.execution import InvalidOutput, RunError, with_budget
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Contract, Evidence, SourceType
from travelmind.schemas.research_quality import (
    EvidenceQuality,
    ModelQualityScore,
    QualityScore,
    grade_evidence,
)

if TYPE_CHECKING:
    from travelmind.runtime.model import StructuredModel

logger = logging.getLogger(__name__)
QualityGrade = Literal["A", "B", "C", "D"]
QualityAction = Literal["use", "cross_verify", "supplement_search", "exclude"]
ACTIONS: dict[QualityGrade, QualityAction] = {
    "A": "use",
    "B": "cross_verify",
    "C": "supplement_search",
    "D": "exclude",
}
SEMANTIC_BATCH_SIZE = 4
SEMANTIC_EXCERPT_LIMIT = 4_000
SEMANTIC_CACHE_SIZE = 256


class Weights(NamedTuple):
    authority: int
    freshness: int
    relevance: int
    completeness: int
    independence: int


WEIGHTS: dict[SourceType, Weights] = {
    "official": Weights(34, 26, 20, 14, 6),
    "academic": Weights(30, 8, 26, 24, 12),
    "book": Weights(26, 8, 26, 28, 12),
    "web": Weights(18, 20, 24, 22, 16),
    "social": Weights(10, 20, 24, 20, 26),
    "map": Weights(16, 32, 24, 20, 8),
}


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    evidence: Evidence
    assignment_id: UUID
    as_of: datetime
    max_age_days: int | None
    authority: QualityScore
    freshness: QualityScore
    relevance: QualityScore
    completeness: QualityScore
    independence: QualityScore
    traceability: QualityScore
    score: int
    grade: QualityGrade
    reason: str
    target_question: str | None
    quality: EvidenceQuality | None

    @property
    def weights(self) -> Weights:
        return WEIGHTS[self.evidence.source_type]

    @property
    def action(self) -> QualityAction:
        return ACTIONS[self.grade]


class SemanticAssessment(Contract):
    evidence_id: UUID
    relevance: ModelQualityScore
    directness: ModelQualityScore


class SemanticBatch(Contract):
    assessments: list[SemanticAssessment]


class RuleScores(NamedTuple):
    traceability: QualityScore
    authority: QualityScore
    freshness: QualityScore
    relevance: QualityScore
    completeness: QualityScore


class EvidenceQualityScorer:
    """Evidence Center screening with optional budgeted v1 semantic scorecards."""

    def __init__(self, *, model: StructuredModel | None = None, model_id: str | None = None):
        model_id = model_id.strip() if model_id else None
        if (model is None) != (not model_id) or (model_id and len(model_id) > 200):
            raise ValueError("a model and its valid model_id must be supplied together")
        self._model = model
        self._model_id = model_id
        self._semantic_cache: OrderedDict[tuple[str, bytes], SemanticAssessment] = OrderedDict()

    async def assess(
        self,
        request: AgentInput,
        evidence: Sequence[Evidence],
        *,
        as_of: datetime,
        max_age_days: int | None,
        retrieval_scores: Mapping[UUID, float] | None = None,
        verified_origins: Mapping[UUID, str] | None = None,
        require_contract_cards: bool = False,
    ) -> list[EvidenceAssessment]:
        """Return one decision per Evidence, in input order."""
        assignment = request.assignment
        if assignment is None:
            raise ValueError("evidence scoring requires an assignment")
        if as_of.utcoffset() is None:
            raise ValueError("as_of must have a timezone")
        if max_age_days is not None and (type(max_age_days) is not int or max_age_days < 0):
            raise ValueError("max_age_days must be a nonnegative integer or None")
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("evidence IDs must be unique")
        if require_contract_cards and self._model is None:
            raise ValueError("frozen v1 quality cards require a semantic model")
        retrieval = retrieval_scores or {}
        if any(
            type(score) not in (int, float) or not 0 <= score <= 1 for score in retrieval.values()
        ):
            raise ValueError("retrieval scores must be normalized to [0, 1]")
        origins = {
            evidence_id: origin.strip()
            for evidence_id, origin in (verified_origins or {}).items()
            if origin.strip()
        }

        as_of = as_of.astimezone(UTC)
        objective_terms = terms(assignment.objective)
        scores: dict[UUID, RuleScores] = {}
        candidates = []
        for item in evidence:
            trace, authority = provenance(item)
            time = freshness(item, as_of, max_age_days)
            body = body_text(item)
            content = completeness(item, body)
            relevant = relevance(objective_terms, body, retrieval.get(item.evidence_id))
            scores[item.evidence_id] = RuleScores(trace, authority, time, relevant, content)
            if require_contract_cards or (
                trace.score > 0 and time.score == 2 and content.score > 0 and relevant.score < 2
            ):
                candidates.append(item)

        judgments = await self._assess_semantics(request, candidates, as_of, max_age_days)
        eligible_ids = set()
        for evidence_id, dimensions in scores.items():
            trace, _, time, relevant, content = dimensions
            judgment = judgments.get(evidence_id)
            if judgment is not None:
                relevant = QualityScore(
                    score=judgment.relevance.score, reason=judgment.relevance.reason
                )
                scores[evidence_id] = dimensions._replace(relevance=relevant)
            if min(trace.score, time.score, relevant.score, content.score) == 2 and (
                judgment is None or judgment.directness.score == 2
            ):
                eligible_ids.add(evidence_id)

        results = []
        independent_scores = independence(evidence, origins, eligible_ids)
        for item, independent in zip(evidence, independent_scores, strict=True):
            dimensions = scores[item.evidence_id]
            trace, authority, time, relevant, content = dimensions
            quality = None
            if (judgment := judgments.get(item.evidence_id)) is not None:
                model_relevance, directness = judgment.relevance, judgment.directness
                assert self._model_id is not None
                total, level = grade_evidence(
                    trace.score,
                    authority.score,
                    time.score,
                    relevant.score,
                    directness.score,
                    max_age_days=max_age_days,
                )
                quality = EvidenceQuality(
                    evidence_id=item.evidence_id,
                    assignment_id=assignment.assignment_id,
                    as_of=as_of,
                    max_age_days=max_age_days,
                    model_id=self._model_id,
                    traceability=trace,
                    authority=authority,
                    freshness=time,
                    relevance=model_relevance.model_copy(),
                    directness=directness.model_copy(),
                    total_score=total,
                    level=level,
                )
            weighted = (
                sum(
                    dimension.score * weight
                    for dimension, weight in zip(
                        (authority, time, relevant, content, independent),
                        WEIGHTS[item.source_type],
                        strict=True,
                    )
                )
                // 2
            )
            grade, reason = _decide(weighted, dimensions, independent, quality)
            results.append(
                EvidenceAssessment(
                    evidence=item,
                    assignment_id=assignment.assignment_id,
                    as_of=as_of,
                    max_age_days=max_age_days,
                    authority=authority,
                    freshness=time,
                    relevance=relevant,
                    completeness=content,
                    independence=independent,
                    traceability=trace,
                    score=weighted,
                    grade=grade,
                    reason=reason,
                    target_question=assignment.objective if grade in ("B", "C") else None,
                    quality=quality,
                )
            )
        return results

    async def _assess_semantics(
        self,
        request: AgentInput,
        evidence: Sequence[Evidence],
        as_of: datetime,
        max_age_days: int | None,
    ) -> dict[UUID, SemanticAssessment]:
        model = self._model
        if model is None or not evidence:
            return {}

        model_request = request.model_copy(
            update={"evidence": [], "prior_findings": [], "draft": None}
        )
        assert request.assignment is not None
        scope = json.dumps(
            {
                "task_id": str(request.task_id),
                "assignment": request.assignment.model_dump(mode="json"),
                "brief": request.brief.model_dump(mode="json"),
                "as_of": as_of.isoformat(),
                "max_age_days": max_age_days,
                "model_id": self._model_id,
                "policy_version": "v1",
            },
            sort_keys=True,
        )
        scope_hash = hashlib.sha256(scope.encode()).hexdigest()
        pending = []
        results = {}
        for item in evidence:
            key = (scope_hash, hashlib.sha256(item.model_dump_json().encode()).digest())
            if (cached := self._semantic_cache.get(key)) is not None:
                self._semantic_cache.move_to_end(key)
                results[item.evidence_id] = cached
            else:
                pending.append((key, item))

        for start in range(0, len(pending), SEMANTIC_BATCH_SIZE):
            batch = pending[start : start + SEMANTIC_BATCH_SIZE]
            excerpts = {
                item.evidence_id: item.excerpt[:SEMANTIC_EXCERPT_LIMIT] for _, item in batch
            }
            context = {
                "as_of": as_of.isoformat(),
                "max_age_days": max_age_days,
                "evidence": [
                    item.model_dump(mode="json", exclude_none=True, exclude={"excerpt"})
                    | {"excerpt": excerpts[item.evidence_id]}
                    for _, item in batch
                ],
            }

            async def evaluate() -> dict[UUID, SemanticAssessment]:
                response = await model(
                    model_request,
                    "针对 assignment.objective 为每个给定 evidence_id 输出一次独立评分。"
                    "relevance 取 2（直接回答）、1（部分或背景）、0（不相关）。"
                    "directness 取 2（摘录明确陈述事实或规则）、1（需要有限推断）、"
                    "0（无相关事实或只有标题）。正分提供一段尽可能短的逐字摘录。"
                    "每项判断只能使用对应的 excerpt，不能用标题、网址或其他材料代替原文。",
                    SemanticBatch,
                    context=context,
                )
                judgments = {judgment.evidence_id: judgment for judgment in response.assessments}
                if (
                    len(judgments) != len(response.assessments)
                    or judgments.keys() != excerpts.keys()
                ):
                    raise InvalidOutput("quality batch must match the requested evidence IDs")
                for evidence_id, judgment in judgments.items():
                    excerpt = excerpts[evidence_id]
                    for dimension in (judgment.relevance, judgment.directness):
                        if dimension.quote is not None and dimension.quote not in excerpt:
                            raise InvalidOutput("quality quote must occur verbatim in the excerpt")
                return judgments

            try:
                batch_results = await with_budget(evaluate, request.budget)
            except RunError as error:
                logger.warning(
                    "evidence.quality_unavailable task_id=%s run_id=%s code=%s",
                    request.task_id,
                    request.run_id,
                    error.info.code,
                )
                continue
            for key, item in batch:
                self._semantic_cache[key] = batch_results[item.evidence_id]
                self._semantic_cache.move_to_end(key)
                if len(self._semantic_cache) > SEMANTIC_CACHE_SIZE:
                    self._semantic_cache.popitem(last=False)
            results.update(batch_results)
        return results


def _decide(
    weighted: int,
    dimensions: RuleScores,
    independent: QualityScore,
    quality: EvidenceQuality | None,
) -> tuple[QualityGrade, str]:
    trace, authority, time, relevant, content = dimensions
    for dimension in (trace, time, relevant, content):
        if dimension.score == 0:
            return "D", dimension.reason
    if quality is not None and quality.directness.score == 0:
        return "D", quality.directness.reason
    for dimension in (time, relevant, content):
        if dimension.score == 1:
            return "C", dimension.reason
    if weighted < 70:
        return "C", "综合分低于 70，需要补查原始或更完整的材料"
    if independent.score == 0:
        return "B", independent.reason
    if quality is not None and quality.directness.score == 1:
        return "B", quality.directness.reason
    if weighted >= 85 and (authority.score == 2 or independent.score == 2):
        return "A", "综合分达到 85，且来源命中权威规则或已有核实的独立发布源"
    return "B", "材料可供核对，需寻找独立来源验证"
