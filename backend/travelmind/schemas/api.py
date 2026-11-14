from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StringConstraints, model_validator

from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.domain import (
    Contract,
    Evidence,
    ReportDraft,
    ResearchBrief,
    TaskStatus,
    Text,
)

IdempotencyKey = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[!-~]+$")
]


class ClarificationQuestion(Contract):
    question_id: UUID
    text: Text


class PendingClarification(Contract):
    clarification_id: UUID
    questions: list[ClarificationQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_questions(self) -> Self:
        if len({question.question_id for question in self.questions}) != len(self.questions):
            raise ValueError("question IDs must be unique")
        return self


class ClarificationAnswer(Contract):
    question_id: UUID
    text: Annotated[Text, Field(max_length=4000)]


class ClarificationAnswers(Contract):
    answers: list[ClarificationAnswer] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_answers(self) -> Self:
        if len({answer.question_id for answer in self.answers}) != len(self.answers):
            raise ValueError("each question may be answered once")
        return self


class CurrentUser(Contract):
    user_id: UUID
    display_name: str | None
    csrf_token: Text


class TaskRead(Contract):
    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    trace_id: UUID
    brief: ResearchBrief
    status: TaskStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    pause_requested_at: AwareDatetime | None = None
    pending_clarification: PendingClarification | None = None


class EvidenceRead(Evidence):
    model_config = ConfigDict(from_attributes=True)

    task_id: UUID


class ReportRead(Contract):
    model_config = ConfigDict(from_attributes=True)

    report_id: UUID
    task_id: UUID
    version: int
    content: ReportDraft
    created_at: AwareDatetime


class ErrorResponse(Contract):
    detail: ErrorInfo


class Liveness(Contract):
    status: Literal["ok"] = "ok"


DependencyStatus = Literal["ok", "not_configured", "unavailable"]


class Readiness(Contract):
    status: Literal["ready", "unavailable"]
    postgres: DependencyStatus
    redis: DependencyStatus
    deepseek: Literal["configured", "not_configured"]
