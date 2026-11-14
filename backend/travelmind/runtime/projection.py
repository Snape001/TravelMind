from datetime import datetime
from uuid import UUID, uuid5

from langgraph.types import StateSnapshot

from travelmind.runtime.state import output_for
from travelmind.schemas.api import ReportRead
from travelmind.schemas.events import (
    AgentFinishedEvent,
    AgentStartedEvent,
    ClarificationEvent,
    ReportReviewedEvent,
    TaskStatusEvent,
)
from travelmind.schemas.worker import ProjectedEvent, ProjectedRun, ProjectionBatch


def batches(task_id: UUID, state: StateSnapshot) -> list[ProjectionBatch]:
    values = state.values
    checkpoint = state.config["configurable"]["checkpoint_id"]
    result = []
    indexes = set(values.get("projection_indexes", ()))
    if indexes:
        [index] = indexes
        started: dict[UUID, tuple[AgentStartedEvent, datetime]] = {}
        runs, events, reports = [], [], []
        for position, entry in enumerate(values.get("journal", ())):
            payload, at = entry["payload"], entry["occurred_at"]
            if isinstance(payload, (TaskStatusEvent, ClarificationEvent)):
                continue
            events.append(
                ProjectedEvent(
                    projection_key=uuid5(task_id, f"{checkpoint}:{index}:{position}"),
                    payload=payload,
                )
            )
            if isinstance(payload, AgentStartedEvent):
                started[payload.run_id] = (payload, at)
            elif isinstance(payload, AgentFinishedEvent):
                event, start = started.pop(payload.run_id)
                results = values.get("results", {})
                runs.append(
                    ProjectedRun(
                        run_id=payload.run_id,
                        agent_id=payload.agent_id,
                        assignment_id=event.assignment_id,
                        status=payload.status,
                        result=output_for(values, payload.run_id)
                        if payload.run_id in results or str(payload.run_id) in results
                        else None,
                        started_at=start,
                        finished_at=at,
                    )
                )
            elif isinstance(payload, ReportReviewedEvent):
                reports.append(
                    ReportRead(
                        report_id=payload.report_id,
                        task_id=task_id,
                        version=values["review_round"],
                        content=values["draft"],
                        created_at=at,
                    )
                )
        status = values.get("status")
        transition = None
        error = None
        if not state.next:
            if status == "completed":
                transition = "complete"
            elif status == "failed":
                transition, error = "fail", values["failure"]
        consumed = values.get("consumed", ())
        result.append(
            ProjectionBatch(
                checkpoint_id=checkpoint,
                projection_index=index,
                transition=transition,
                error=error,
                consumed_instruction_id=consumed[0] if consumed else None,
                events=events,
                runs=runs,
                reports=reports,
            )
        )
    if state.interrupts:
        [halt] = state.interrupts
        clarifying = halt.value["kind"] == "clarification"
        result.append(
            ProjectionBatch(
                checkpoint_id=checkpoint,
                projection_index=halt.value["projection_index"],
                transition="clarification_required" if clarifying else "pause_reached",
                interrupt_id=halt.id,
                clarification=values["pending_clarification"] if clarifying else None,
            )
        )
    return result
