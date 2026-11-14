from typing import Annotated, Required, TypedDict, TypeVar
from uuid import UUID

from travelmind.schemas.agents import AgentOutput
from travelmind.schemas.domain import Evidence, ReportDraft, ResearchBrief, ResearchPlan, TaskStatus

Record = TypeVar("Record")


def merge_records(left: dict[UUID, Record], right: dict[UUID, Record]) -> dict[UUID, Record]:
    """Merge immutable, ID-keyed records from parallel nodes and checkpoint replays."""
    merged = left.copy()
    for record_id, record in right.items():
        if record_id in merged and merged[record_id] != record:
            raise ValueError(f"conflicting records for {record_id}")
        merged[record_id] = record
    return merged


class ResearchState(TypedDict, total=False):
    task_id: Required[UUID]
    trace_id: Required[UUID]
    brief: Required[ResearchBrief]
    status: TaskStatus
    plan: ResearchPlan
    results: Annotated[dict[UUID, AgentOutput], merge_records]
    evidence: Annotated[dict[UUID, Evidence], merge_records]
    draft: ReportDraft
    review_round: int
