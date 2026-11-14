from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

AgentId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[Text, Field(max_length=200)]
ResearchDepth = Literal["quick", "deep", "academic"]
TaskStatus = Literal["queued", "running", "needs_clarification", "paused", "completed", "failed"]
SourceType = Literal["web", "official", "academic", "book", "social", "map"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchBrief(Contract):
    destination: ShortText
    topics: list[ShortText] = Field(default_factory=list, max_length=20)
    requirements: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""
    depth: ResearchDepth = "quick"


class Assignment(Contract):
    assignment_id: UUID
    agent_id: AgentId
    objective: Text
    depends_on: list[UUID] = Field(default_factory=list)


class ResearchPlan(Contract):
    objective: Text
    assignments: list[Assignment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        dependencies = {
            assignment.assignment_id: assignment.depends_on for assignment in self.assignments
        }
        if len(dependencies) != len(self.assignments):
            raise ValueError("assignment IDs must be unique")
        if any(set(parents) - dependencies.keys() for parents in dependencies.values()):
            raise ValueError("assignment dependency must reference an existing assignment")
        try:
            TopologicalSorter(dependencies).prepare()
        except CycleError as exc:
            raise ValueError("assignment dependencies contain a cycle") from exc
        return self


class Evidence(Contract):
    evidence_id: UUID
    source_type: SourceType
    source_title: Text
    source_url: HttpUrl | None = None
    source_locator: Text | None = None
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    excerpt: Text

    @model_validator(mode="after")
    def require_source_reference(self) -> Self:
        if self.source_url is None and self.source_locator is None:
            raise ValueError("evidence requires a source URL or bibliographic locator")
        return self


class Finding(Contract):
    statement: Text
    evidence_ids: list[UUID] = Field(default_factory=list)
    support: Literal["supported", "insufficient", "disputed"]

    @model_validator(mode="after")
    def require_support_reference(self) -> Self:
        if self.support in ("supported", "disputed") and not self.evidence_ids:
            raise ValueError("supported and disputed findings require evidence IDs")
        return self


class ReportSection(Contract):
    heading: Text
    body: Text
    author_agent_ids: list[AgentId] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)


class ReportDraft(Contract):
    title: Text
    sections: list[ReportSection] = Field(min_length=1)


class ReviewIssue(Contract):
    category: Literal["accuracy", "citation", "coverage", "consistency"]
    message: Text
    evidence_ids: list[UUID] = Field(default_factory=list)


class Review(Contract):
    decision: Literal["approved", "revision_required"]
    issues: list[ReviewIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_revision_feedback(self) -> Self:
        if self.decision == "revision_required" and not self.issues:
            raise ValueError("revision requires at least one actionable issue")
        return self
