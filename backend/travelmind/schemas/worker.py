from typing import Annotated, Literal, Protocol, Self, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from travelmind.schemas.agents import AgentOutput, ErrorInfo
from travelmind.schemas.api import ClarificationAnswers, PendingClarification, ReportRead
from travelmind.schemas.domain import AgentId, Contract, ResearchBrief, Text
from travelmind.schemas.events import ClarificationEvent, EventPayload, TaskStatusEvent
from travelmind.schemas.map_data import MapData

LeaseEpoch = Annotated[int, Field(ge=1)]
LeaseSeconds = Annotated[int, Field(ge=1)]


class LeaseRef(Contract):
    task_id: UUID
    worker_id: UUID
    lease_epoch: LeaseEpoch


class TaskClaim(Contract):
    lease: LeaseRef
    trace_id: UUID
    lease_until: AwareDatetime
    reclaimed: bool
    max_attempts: int = Field(ge=1, strict=True)
    attempt_count: int = Field(ge=1, strict=True)
    execution_permitted: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt count cannot exceed the task budget")
        if not self.execution_permitted and (
            not self.reclaimed or self.attempt_count != self.max_attempts
        ):
            raise ValueError("projection-only claims require a reclaimed, exhausted task")
        return self


class Instruction(Contract):
    instruction_id: UUID


class StartResearch(Instruction):
    kind: Literal["start"]
    brief: ResearchBrief


class ContinueCheckpoint(Instruction):
    kind: Literal["continue_checkpoint"]


class ResumePause(Instruction):
    kind: Literal["resume_pause"]
    interrupt_id: Text


class AnswerClarification(Instruction):
    kind: Literal["answer_clarification"]
    interrupt_id: Text
    clarification_id: UUID
    response: ClarificationAnswers


ExecutionInstruction = Annotated[
    StartResearch | ContinueCheckpoint | ResumePause | AnswerClarification,
    Field(discriminator="kind"),
]


class WorkerInstruction(Contract):
    instruction: ExecutionInstruction
    pause_requested_at: AwareDatetime | None = None
    projected_index: int = Field(ge=0)


class ProjectedEvent(Contract):
    projection_key: UUID
    payload: EventPayload


class ProjectedRun(Contract):
    run_id: UUID
    agent_id: AgentId
    assignment_id: UUID | None = None
    status: Literal["running", "completed", "failed"]
    result: AgentOutput | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class ProjectionBatch(Contract):
    checkpoint_id: Text
    projection_index: int = Field(ge=1)
    transition: Literal["pause_reached", "clarification_required", "complete", "fail"] | None = None
    interrupt_id: Text | None = None
    clarification: PendingClarification | None = None
    consumed_instruction_id: UUID | None = None
    error: ErrorInfo | None = None
    events: list[ProjectedEvent] = Field(default_factory=list)
    runs: list[ProjectedRun] = Field(default_factory=list)
    reports: list[ReportRead] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        interrupt = self.transition in ("pause_reached", "clarification_required")
        if interrupt != (self.interrupt_id is not None):
            raise ValueError("an interrupt ID is required only for a pause or clarification")
        if (self.transition == "clarification_required") != (self.clarification is not None):
            raise ValueError("clarification details are required only for a clarification")
        if (self.transition == "fail") != (self.error is not None):
            raise ValueError("an error is required only for a failure")
        if len({item.projection_key for item in self.events}) != len(self.events):
            raise ValueError("projection keys must be unique within the batch")
        if any(
            isinstance(item.payload, (TaskStatusEvent, ClarificationEvent)) for item in self.events
        ):
            raise ValueError(
                "task status and clarification events are materialized from transitions"
            )
        return self


class MapProjectionBatch(ProjectionBatch):
    map_data: MapData


class AttemptFailure(Contract):
    failure_id: UUID
    error: ErrorInfo


class LeaseLost(Exception):
    """No live running task matches this lease."""


class ProjectionConflict(Exception):
    """The projection violates the task's durable progress or state."""


WriteTransaction = TypeVar("WriteTransaction", contravariant=True)


class WorkerStore(Protocol[WriteTransaction]):
    async def claim(self, worker_id: UUID, *, lease_seconds: LeaseSeconds) -> TaskClaim | None: ...

    async def renew(self, lease: LeaseRef, *, lease_seconds: LeaseSeconds) -> AwareDatetime: ...

    async def load_instruction(self, lease: LeaseRef) -> WorkerInstruction: ...

    async def commit_projection(
        self, lease: LeaseRef, projection: ProjectionBatch | AttemptFailure
    ) -> Literal["applied", "already_applied"]: ...

    async def check_lease(self, lease: LeaseRef, *, transaction: WriteTransaction) -> None:
        """Lock the task and require a live execution lease in the caller's write transaction."""
        ...
