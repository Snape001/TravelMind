from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.domain import AgentId, Contract, ResearchPlan, TaskStatus, Text

EventSequence = Annotated[int, Field(ge=1, le=2**53 - 1)]
EventCursor = Annotated[int, Field(ge=0, le=2**53 - 1)]


class TaskStatusEvent(Contract):
    type: Literal["task.status"]
    status: TaskStatus
    error: ErrorInfo | None = None


class ClarificationEvent(Contract):
    type: Literal["task.clarification"]
    questions: list[Text] = Field(min_length=1)


class PlanReadyEvent(Contract):
    type: Literal["plan.ready"]
    plan: ResearchPlan


class RunEvent(Contract):
    agent_id: AgentId
    run_id: UUID


class AgentStartedEvent(RunEvent):
    type: Literal["agent.started"]
    assignment_id: UUID | None = None


class AgentMessageEvent(RunEvent):
    type: Literal["agent.message"]
    message: Text


class AgentFinishedEvent(RunEvent):
    type: Literal["agent.finished"]
    status: Literal["completed", "failed"]
    error: ErrorInfo | None = None


class ToolStartedEvent(RunEvent):
    type: Literal["tool.started"]
    tool_call_id: UUID
    tool_name: Text


class ToolFinishedEvent(RunEvent):
    type: Literal["tool.finished"]
    tool_call_id: UUID
    duration_ms: int = Field(ge=0)
    status: Literal["completed", "failed"]
    error: ErrorInfo | None = None


class EvidenceAddedEvent(RunEvent):
    type: Literal["evidence.added"]
    evidence_ids: list[UUID] = Field(min_length=1)


class ReportReadyEvent(RunEvent):
    type: Literal["report.ready"]
    report_id: UUID
    version: int = Field(ge=1)


class ReportReviewedEvent(RunEvent):
    type: Literal["report.reviewed"]
    report_id: UUID
    decision: Literal["approved", "revision_required"]


EventPayload = Annotated[
    TaskStatusEvent
    | ClarificationEvent
    | PlanReadyEvent
    | AgentStartedEvent
    | AgentMessageEvent
    | AgentFinishedEvent
    | ToolStartedEvent
    | ToolFinishedEvent
    | EvidenceAddedEvent
    | ReportReadyEvent
    | ReportReviewedEvent,
    Field(discriminator="type"),
]


class AgentEvent(Contract):
    version: Literal[1] = 1
    event_id: UUID
    task_id: UUID
    trace_id: UUID
    sequence: EventSequence
    occurred_at: AwareDatetime
    payload: EventPayload
