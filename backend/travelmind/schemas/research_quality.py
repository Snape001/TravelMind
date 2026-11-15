from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from travelmind.schemas.agents import ResearchResult
from travelmind.schemas.domain import Contract, Evidence, Finding, ResearchBrief, ShortText, Text

QualityLevel = Literal["high", "medium", "low"]
ReflectionState = Literal["sufficient", "insufficient", "low_quality", "conflicting"]
ReflectionStrategy = Literal[
    "accept", "expand_search", "replace_sources", "verify_conflict", "stop"
]
ReflectionStopReason = Literal[
    "sufficient", "round_limit", "no_new_evidence", "budget_exhausted", "assessment_unavailable"
]
DimensionScore = Annotated[int, Field(strict=True, ge=0, le=2)]
ReflectionRound = Annotated[int, Field(strict=True, ge=0, le=2)]

FOLLOW_UP: dict[ReflectionState, ReflectionStrategy] = {
    "insufficient": "expand_search",
    "low_quality": "replace_sources",
    "conflicting": "verify_conflict",
}


def grade_evidence(
    traceability: int,
    authority: int,
    freshness: int,
    relevance: int,
    directness: int,
    *,
    max_age_days: int | None,
) -> tuple[int, QualityLevel]:
    total = traceability + authority + freshness + relevance + directness
    minimum_freshness = 1 if max_age_days is None else 2
    if min(traceability, relevance, directness) == 0 or freshness < minimum_freshness or total < 5:
        return total, "low"
    return total, "high" if total >= 8 else "medium"


class QualityScore(Contract):
    score: DimensionScore
    reason: Text


class ModelQualityScore(QualityScore):
    quote: Text | None = None

    @model_validator(mode="after")
    def require_positive_basis(self) -> Self:
        if self.score > 0 and self.quote is None:
            raise ValueError("positive model scores require a source quote")
        return self


class EvidenceQuality(Contract):
    evidence_id: UUID
    assignment_id: UUID
    as_of: AwareDatetime
    max_age_days: Annotated[int, Field(strict=True, ge=0)] | None
    policy_version: Literal["v1"] = "v1"
    model_id: ShortText
    traceability: QualityScore
    authority: QualityScore
    freshness: QualityScore
    relevance: ModelQualityScore
    directness: ModelQualityScore
    total_score: int = Field(strict=True, ge=0, le=10)
    level: QualityLevel

    @model_validator(mode="after")
    def validate_grade(self) -> Self:
        total, level = grade_evidence(
            self.traceability.score,
            self.authority.score,
            self.freshness.score,
            self.relevance.score,
            self.directness.score,
            max_age_days=self.max_age_days,
        )
        if self.total_score != total or self.level != level:
            raise ValueError("total score or level is inconsistent with the v1 quality rubric")
        return self


class ReflectionAction(Contract):
    state: ReflectionState
    strategy: ReflectionStrategy
    round: ReflectionRound
    max_rounds: ReflectionRound
    stop_reason: ReflectionStopReason | None = None
    reason: Text
    target_questions: list[Text] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    model_id: ShortText | None = None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.round > self.max_rounds:
            raise ValueError("reflection round exceeds its limit")
        ids = set(self.evidence_ids)
        if len(ids) != len(self.evidence_ids):
            raise ValueError("reflection evidence IDs must be unique")
        if self.strategy in ("accept", "stop"):
            if self.target_questions:
                raise ValueError("terminal actions cannot have follow-up questions")
        elif not self.target_questions:
            raise ValueError("follow-up requires target questions")
        if self.state == "sufficient":
            if self.strategy != "accept" or self.stop_reason != "sufficient":
                raise ValueError("sufficient research must be accepted and stopped")
            if not ids or self.model_id is None:
                raise ValueError("sufficient research requires evidence and a model judgment")
        elif self.strategy == "stop":
            if self.stop_reason in (None, "sufficient"):
                raise ValueError("unresolved research requires a non-success stop reason")
            if self.stop_reason == "round_limit" and self.round != self.max_rounds:
                raise ValueError("round limit has not been reached")
            if self.stop_reason == "no_new_evidence" and self.round == 0:
                raise ValueError("no_new_evidence requires a follow-up round")
            if self.stop_reason == "assessment_unavailable" and self.state != "insufficient":
                raise ValueError("an unavailable assessment must be recorded as insufficient")
        elif (
            self.strategy != FOLLOW_UP[self.state]
            or self.round == self.max_rounds
            or self.stop_reason is not None
        ):
            raise ValueError("follow-up is inconsistent with the state or remaining rounds")
        if self.state == "conflicting" and (len(ids) < 2 or self.model_id is None):
            raise ValueError("conflicts require competing evidence and a model judgment")
        return self


class ExpertResearchResult(Contract):
    assignment_id: UUID
    research: ResearchResult
    quality: list[EvidenceQuality]
    reflection: ReflectionAction
    unresolved_questions: list[Text]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        evidence = {item.evidence_id: item for item in self.research.evidence}
        if len(evidence) != len(self.research.evidence):
            raise ValueError("expert evidence IDs must be unique")
        if set(self.reflection.evidence_ids) - evidence.keys() or any(
            set(finding.evidence_ids) - evidence.keys() for finding in self.research.findings
        ):
            raise ValueError("expert findings and reflection must reference supplied evidence")

        if len({(item.as_of, item.max_age_days) for item in self.quality}) > 1:
            raise ValueError("quality assessments must use the same assignment time and window")
        quality_ids: set[UUID] = set()
        usable_ids: set[UUID] = set()
        for quality in self.quality:
            evidence_id = quality.evidence_id
            if (
                quality.assignment_id != self.assignment_id
                or evidence_id not in evidence
                or evidence_id in quality_ids
            ):
                raise ValueError("quality must match the assignment and reference unique evidence")
            quality_ids.add(evidence_id)
            excerpt = evidence[evidence_id].excerpt
            if any(
                dimension.quote is not None and dimension.quote not in excerpt
                for dimension in (quality.relevance, quality.directness)
            ):
                raise ValueError("model quotes must occur in the supplied evidence excerpt")
            if quality.level != "low":
                usable_ids.add(evidence_id)
        if any(
            finding.support == "supported" and not usable_ids.intersection(finding.evidence_ids)
            for finding in self.research.findings
        ):
            raise ValueError("supported findings require at least one usable quality assessment")
        cited = usable_ids.intersection(self.reflection.evidence_ids)
        if self.reflection.state == "sufficient" and not cited:
            raise ValueError("acceptance must cite usable evidence")
        if self.reflection.state == "conflicting" and len(cited) < 2:
            raise ValueError("conflict must cite at least two usable evidence records")
        return self

    @model_validator(mode="after")
    def validate_final_reflection(self) -> Self:
        if self.reflection.strategy == "accept":
            if (
                self.unresolved_questions
                or not self.research.findings
                or any(finding.support != "supported" for finding in self.research.findings)
            ):
                raise ValueError(
                    "accepted research requires supported findings without open questions"
                )
        elif self.reflection.strategy != "stop" or not self.unresolved_questions:
            raise ValueError("an unresolved result requires a terminal action and open questions")
        return self


class EvaluationCase(Contract):
    case_id: UUID
    brief: ResearchBrief
    question: Text
    as_of: AwareDatetime
    reference_answer: list[Finding] = Field(min_length=1)
    reference_evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        evidence_ids = {item.evidence_id for item in self.reference_evidence}
        if len(evidence_ids) != len(self.reference_evidence):
            raise ValueError("reference evidence IDs must be unique")
        if any(set(finding.evidence_ids) - evidence_ids for finding in self.reference_answer):
            raise ValueError("reference answers must cite evidence from the evaluation case")
        return self
