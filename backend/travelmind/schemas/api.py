from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict

from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.domain import Contract, Evidence, ReportDraft, ResearchBrief, TaskStatus


class TaskRead(Contract):
    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    trace_id: UUID
    brief: ResearchBrief
    status: TaskStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


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
