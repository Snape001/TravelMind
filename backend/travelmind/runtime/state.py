from typing import Annotated, NotRequired, TypedDict, TypeVar
from uuid import UUID

from langgraph.channels import Topic
from pydantic import TypeAdapter

from travelmind.runtime.journal import JournalEvent
from travelmind.schemas.agents import AgentOutput, ErrorInfo
from travelmind.schemas.api import PendingClarification
from travelmind.schemas.domain import (
    Evidence,
    ReportDraft,
    ResearchBrief,
    ResearchPlan,
    Review,
    TaskStatus,
)

Record = TypeVar("Record")
agent_output = TypeAdapter(AgentOutput)


def merge_records(
    left: dict[UUID | str, Record], right: dict[UUID | str, Record]
) -> dict[UUID | str, Record]:
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
    start_instruction_id: NotRequired[UUID]


class ResearchState(ResearchInput, total=False):
    journal: Annotated[list[JournalEvent], Topic(JournalEvent)]
    projection_indexes: Annotated[list[int], Topic(int)]
    consumed: Annotated[list[UUID], Topic(UUID)]
    pending_clarification: PendingClarification
    clarification_history: list[dict[str, str]]
    status: TaskStatus
    plan: ResearchPlan
    director_run_id: UUID
    questions: list[str]
    failure: ErrorInfo
    results: Annotated[dict[UUID | str, AgentOutput | dict], merge_records]
    evidence: Annotated[dict[UUID | str, Evidence | dict], merge_records]
    assignment_runs: Annotated[dict[UUID | str, UUID], merge_records]
    challenge_runs: Annotated[dict[UUID | str, UUID], merge_records]
    expert_research: Annotated[dict[str, dict], merge_records]
    reflection_rounds: Annotated[dict[str, dict], merge_records]
    reflection_decisions: Annotated[dict[str, dict], merge_records]
    reflection_next: list[dict]
    reflection_exchange_used: bool
    wave: list[UUID]
    usable_research: list[UUID]
    draft: ReportDraft
    report_id: UUID
    review: Review
    review_round: int


def output_for(state: ResearchState, run_id: UUID) -> AgentOutput:
    results = state["results"]
    value = results[run_id] if run_id in results else results[str(run_id)]
    return agent_output.validate_python(value) if isinstance(value, dict) else value


def evidence_items(state: ResearchState) -> dict[UUID, Evidence]:
    return {
        UUID(str(key)): Evidence.model_validate(value) if isinstance(value, dict) else value
        for key, value in state["evidence"].items()
    }


def run_mapping(records: dict) -> dict[UUID, UUID]:
    return {UUID(str(key)): value for key, value in records.items()}
