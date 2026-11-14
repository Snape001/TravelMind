from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from travelmind.schemas.domain import (
    AgentId,
    Assignment,
    Contract,
    Evidence,
    Finding,
    ReportDraft,
    ResearchBrief,
    ResearchPlan,
    Review,
    Text,
)


class RunBudget(Contract):
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_attempts: int = Field(default=2, ge=1, le=5)
    max_output_tokens: int = Field(default=4096, ge=1)


class AgentInput(Contract):
    task_id: UUID
    trace_id: UUID
    run_id: UUID
    agent_id: AgentId
    brief: ResearchBrief
    assignment: Assignment | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    prior_findings: list[Finding] = Field(default_factory=list)
    draft: ReportDraft | None = None
    budget: RunBudget = Field(default_factory=RunBudget)


class ErrorInfo(Contract):
    code: Text
    message: Text
    retryable: bool


class AgentResult(Contract):
    run_id: UUID
    agent_id: AgentId


class BriefResult(AgentResult):
    kind: Literal["brief"]
    brief: ResearchBrief


class ClarificationResult(AgentResult):
    kind: Literal["clarification"]
    questions: list[Text] = Field(min_length=1)


class PlanResult(AgentResult):
    kind: Literal["plan"]
    plan: ResearchPlan


class ResearchResult(AgentResult):
    kind: Literal["research"]
    findings: list[Finding]
    evidence: list[Evidence] = Field(default_factory=list)


class ReportResult(AgentResult):
    kind: Literal["report"]
    report: ReportDraft


class ReviewResult(AgentResult):
    kind: Literal["review"]
    review: Review


class AgentFailure(AgentResult):
    kind: Literal["failed"]
    error: ErrorInfo


AgentOutput = Annotated[
    BriefResult
    | ClarificationResult
    | PlanResult
    | ResearchResult
    | ReportResult
    | ReviewResult
    | AgentFailure,
    Field(discriminator="kind"),
]
