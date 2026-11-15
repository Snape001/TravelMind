import hashlib
import json
from collections import Counter
from typing import Annotated, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl, StringConstraints, model_validator

from travelmind.schemas.agents import ResearchResult
from travelmind.schemas.domain import (
    Contract,
    Evidence,
    Finding,
    ReportDraft,
    ResearchBrief,
    ResearchDepth,
    ShortText,
    SourceType,
    TaskStatus,
    Text,
)
from travelmind.schemas.events import AgentEvent
from travelmind.schemas.research_quality import EvaluationCase, ExpertResearchResult

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0)]
Support = Literal["supported", "insufficient", "disputed"]


def stable_id(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"travelmind/evaluation/{kind}/{key}")


def fingerprint(model: BaseModel) -> str:
    canonical = json.dumps(
        model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Source(Contract):
    title: Text
    url: HttpUrl
    type: SourceType = "official"
    locator: Text
    observed_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    excerpt: Text

    def evidence(self, key: str, as_of: AwareDatetime) -> Evidence:
        return Evidence(
            evidence_id=stable_id("source", key),
            source_type=self.type,
            source_title=self.title,
            source_url=self.url,
            source_locator=self.locator,
            retrieved_at=self.observed_at or as_of,
            published_at=self.published_at,
            excerpt=self.excerpt,
        )


class Reference(Contract):
    statement: Text
    sources: list[Key] = Field(min_length=1)
    support: Support = "supported"


class HumanReview(Contract):
    reviewer: ShortText
    verified_at: AwareDatetime
    notes: Text


class Case(Contract):
    key: Key
    destination: ShortText
    topic: ShortText
    question: Text
    status: Literal["pending", "gold"] = "pending"
    human_review: HumanReview | None = None
    depth: ResearchDepth = "quick"
    as_of: AwareDatetime | None = None
    answer: list[Reference] = Field(min_length=1)

    @model_validator(mode="after")
    def require_review_for_gold(self) -> Self:
        if (self.status == "gold") != (self.human_review is not None):
            raise ValueError("gold requires explicit human review; pending must be unsigned")
        return self


class Dataset(Contract):
    version: ShortText
    as_of: AwareDatetime
    sources: dict[Key, Source]
    cases: list[Case] = Field(min_length=1)

    def reference(self, case: Case) -> EvaluationCase:
        keys = dict.fromkeys(key for item in case.answer for key in item.sources)
        return EvaluationCase(
            case_id=stable_id("case", case.key),
            brief=ResearchBrief(
                destination=case.destination,
                topics=[case.topic],
                requirements=case.question,
                depth=case.depth,
            ),
            question=case.question,
            as_of=case.as_of or self.as_of,
            reference_answer=[
                Finding(
                    statement=item.statement,
                    support=item.support,
                    evidence_ids=[stable_id("source", key) for key in item.sources],
                )
                for item in case.answer
            ],
            reference_evidence=[self.sources[key].evidence(key, self.as_of) for key in keys],
        )

    def inventory(self) -> dict[str, dict[str, int]]:
        return {
            destination: dict(
                Counter(case.status for case in self.cases if case.destination == destination)
            )
            for destination in sorted({case.destination for case in self.cases})
        }

    @model_validator(mode="after")
    def validate_cases(self) -> Self:
        if len({case.key for case in self.cases}) != len(self.cases):
            raise ValueError("case keys must be unique")
        for case in self.cases:
            for item in case.answer:
                if (
                    len(set(item.sources)) != len(item.sources)
                    or set(item.sources) - self.sources.keys()
                ):
                    raise ValueError(f"{case.key} has duplicate or unknown reference sources")
            self.reference(case)
        return self


class Usage(Contract):
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    model_calls: Count | None = None
    duration_ms: Count | None = None

    def observations(self) -> dict[str, int | None]:
        total = None
        if self.input_tokens is not None and self.output_tokens is not None:
            total = self.input_tokens + self.output_tokens
        return self.model_dump() | {"total_tokens": total}


class CandidateRun(Contract):
    case_key: Key
    task_id: UUID
    origin: Literal["recorded", "fixture_replay", "demo"]
    source: Text
    status: TaskStatus
    failure_code: ShortText | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    ranked_evidence_ids: list[UUID] | None = None
    research: list[ResearchResult] | None = None
    expert_research: list[ExpertResearchResult] | None = None
    report: ReportDraft | None = None
    report_id: UUID | None = None
    events: list[AgentEvent] | None = None
    usage: Usage = Field(default_factory=Usage)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("candidate evidence IDs must be unique")
        if set(self.ranked_evidence_ids or ()) - evidence_ids:
            raise ValueError("ranked evidence IDs must resolve against candidate evidence")
        if self.report_id is not None and self.report is None:
            raise ValueError("a report ID requires a report snapshot")
        return self


class RunBatch(Contract):
    schema_version: Literal["v1"] = "v1"
    target_commit: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{40}$")]
    runs: list[CandidateRun] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        if len({run.task_id for run in self.runs}) != len(self.runs):
            raise ValueError("each task must occur once in a run batch")
        return self


class ClaimJudgment(Contract):
    section: Count
    quote: Text
    verdict: Literal["supported", "unsupported", "unavailable"]
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    reason: Text

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        ids = self.supporting_evidence_ids
        if len(set(ids)) != len(ids) or (self.verdict == "supported") != bool(ids):
            raise ValueError("supported claims require unique supporting evidence IDs")
        return self


class CoverageJudgment(Contract):
    reference: Count
    verdict: Literal["covered", "missing", "contradicted", "unavailable"]
    claim: Count | None = None
    reason: Text

    @model_validator(mode="after")
    def require_candidate_claim(self) -> Self:
        if (self.verdict in {"covered", "contradicted"}) != (self.claim is not None):
            raise ValueError("covered and contradicted references require a candidate claim")
        return self


class Judgment(Contract):
    case_key: Key
    task_id: UUID
    run_sha256: Digest
    case_sha256: Digest
    judge_id: ShortText
    judge_type: Literal["human", "model"]
    inventory_complete: bool
    claims: list[ClaimJudgment]
    coverage: list[CoverageJudgment]
