from typing import Annotated, TypedDict, TypeVar
from uuid import UUID

from travelmind.schemas.agents import AgentOutput, ErrorInfo
from travelmind.schemas.domain import (
    Evidence,
    ReportDraft,
    ResearchBrief,
    ResearchPlan,
    Review,
    TaskStatus,
)

Record = TypeVar("Record")


def merge_records(left: dict[UUID, Record], right: dict[UUID, Record]) -> dict[UUID, Record]:
    """Merge immutable, ID-keyed records from parallel nodes and checkpoint replays."""
    merged = left.copy()
    for record_id, record in right.items():
        if record_id in merged and merged[record_id] != record:
            raise ValueError(f"conflicting records for {record_id}")
        merged[record_id] = record
    return merged


class ResearchInput(TypedDict):
    task_id: UUID
    trace_id: UUID
    brief: ResearchBrief


class ResearchState(ResearchInput, total=False):
    status: TaskStatus
    plan: ResearchPlan
    questions: list[str]
    failure: ErrorInfo
    results: Annotated[dict[UUID, AgentOutput], merge_records]
    evidence: Annotated[dict[UUID, Evidence], merge_records]
    assignment_runs: Annotated[dict[UUID, UUID], merge_records]
    challenge_runs: Annotated[dict[UUID, UUID], merge_records]
    wave: list[UUID]
    usable_research: list[UUID]
    draft: ReportDraft
    report_id: UUID
    review: Review
    review_round: int
