from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.evidence import save_evidence
from travelmind.db.models import (
    AgentRunRow,
    ClarificationRow,
    EventRow,
    EvidenceRow,
    ReportRow,
    TaskMapRow,
    TaskRow,
)
from travelmind.db.research_browser import publish_citations
from travelmind.db.tasks import append_event, store_event
from travelmind.schemas.agents import ErrorInfo, ResearchResult
from travelmind.schemas.api import ReportRead
from travelmind.schemas.domain import TaskStatus
from travelmind.schemas.events import (
    ClarificationEvent,
    EvidenceAddedEvent,
    ReportReadyEvent,
    ReportReviewedEvent,
    TaskStatusEvent,
)
from travelmind.schemas.map_data import MapData
from travelmind.schemas.task_control import InvalidTaskTransition, next_task_status
from travelmind.schemas.worker import (
    ContinueCheckpoint,
    MapProjectionBatch,
    ProjectionBatch,
    ProjectionConflict,
)


def release_lease(task: TaskRow) -> None:
    task.lease_worker_id = task.lease_until = task.available_at = None
    task.lease_execution_permitted = None


async def publish_transition(
    session: AsyncSession,
    task: TaskRow,
    status: TaskStatus,
    now: datetime,
    *,
    error: ErrorInfo | None = None,
) -> None:
    await append_event(
        session, task, TaskStatusEvent(type="task.status", status=status, error=error), now=now
    )


async def apply_batch(
    session: AsyncSession, task: TaskRow, batch: ProjectionBatch, now: datetime
) -> None:
    transition = batch.transition
    if transition == "pause_reached" and task.pause_requested_at is None:
        raise ProjectionConflict("no pause has been requested")
    if batch.consumed_instruction_id is not None:
        if task.instruction["instruction_id"] != str(batch.consumed_instruction_id):
            raise ProjectionConflict("consumed instruction does not match")
        task.instruction = ContinueCheckpoint(
            kind="continue_checkpoint", instruction_id=uuid4()
        ).model_dump(mode="json")

    await _write_resources(session, task, batch)
    if isinstance(batch, MapProjectionBatch):
        await _write_task_map(session, task, batch)
    await _write_events(session, task, batch, now)
    if transition is None:
        return
    try:
        status = next_task_status(task.status, transition)
    except InvalidTaskTransition as exc:
        raise ProjectionConflict("projection cannot change the current task state") from exc

    task.pause_requested_at = None
    release_lease(task)
    if transition == "pause_reached":
        task.paused_interrupt_id = batch.interrupt_id
    if batch.clarification is not None:
        clarification = batch.clarification
        if await session.get(ClarificationRow, (task.task_id, clarification.clarification_id)):
            raise ProjectionConflict("clarification ID was already used")
        session.add(
            ClarificationRow(
                task_id=task.task_id,
                clarification_id=clarification.clarification_id,
                questions=clarification.model_dump(mode="json")["questions"],
                interrupt_id=batch.interrupt_id,
                created_at=now,
            )
        )
        await append_event(
            session,
            task,
            ClarificationEvent(
                type="task.clarification",
                questions=[entry.text for entry in clarification.questions],
            ),
            now=now,
        )
    await publish_transition(session, task, status, now, error=batch.error)


async def _write_resources(session: AsyncSession, task: TaskRow, batch: ProjectionBatch) -> None:
    for run in batch.runs:
        row = await session.get(AgentRunRow, run.run_id)
        values = run.model_dump(exclude={"result"})
        values["result"] = run.result.model_dump(mode="json") if run.result else None
        if row is None:
            session.add(AgentRunRow(task_id=task.task_id, **values))
            continue
        if (
            row.task_id != task.task_id
            or row.agent_id != run.agent_id
            or row.assignment_id != run.assignment_id
        ):
            raise ProjectionConflict("run ID already belongs to another execution")
        if row.status != "running" and any(
            getattr(row, field) != value for field, value in values.items()
        ):
            raise ProjectionConflict("finished run cannot be changed")
        for field, value in values.items():
            setattr(row, field, value)

    for report in batch.reports:
        if report.task_id != task.task_id:
            raise ProjectionConflict("report does not belong to the task")
        row = await session.get(ReportRow, report.report_id)
        if row is not None:
            if ReportRead.model_validate(row) != report:
                raise ProjectionConflict("report ID already has different content")
            continue
        version = await session.scalar(
            select(ReportRow.report_id).where(
                ReportRow.task_id == task.task_id, ReportRow.version == report.version
            )
        )
        if version is not None:
            raise ProjectionConflict("report version already exists")
        values = report.model_dump(exclude={"content"})
        session.add(ReportRow(**values, content=report.content.model_dump(mode="json")))


async def _write_task_map(session: AsyncSession, task: TaskRow, batch: MapProjectionBatch) -> None:
    snapshot = batch.map_data
    if snapshot.task_id != task.task_id or snapshot.report_id is not None:
        raise ProjectionConflict("map snapshot does not belong to this task")
    references = {entry.evidence_id for entry in snapshot.routes if entry.evidence_id is not None}
    sources = {
        item.evidence_id: item
        for run in batch.runs
        if run.agent_id == "travel_planning" and isinstance(run.result, ResearchResult)
        for item in run.result.evidence
        if item.source_type == "map"
    }
    if set(sources) != references:
        raise ProjectionConflict("map snapshot evidence differs from the travel result")
    saved = await save_evidence(session, task.task_id, sources.values())
    if {item.evidence_id: item for item in saved} != sources:
        raise ProjectionConflict("map evidence changed during persistence")
    payload = snapshot.model_dump(mode="json")
    row = await session.get(TaskMapRow, task.task_id)
    if row is None:
        session.add(TaskMapRow(task_id=task.task_id, data=payload))
    elif row.data != payload:
        raise ProjectionConflict("published task map cannot be replaced")


async def _write_events(
    session: AsyncSession, task: TaskRow, batch: ProjectionBatch, now: datetime
) -> None:
    if not batch.events:
        return
    sequence = (
        await session.scalar(
            select(func.max(EventRow.sequence)).where(EventRow.task_id == task.task_id)
        )
        or 0
    )
    for event in batch.events:
        existing = await session.scalar(
            select(EventRow.payload).where(
                EventRow.task_id == task.task_id, EventRow.projection_key == event.projection_key
            )
        )
        payload = event.payload
        if existing is not None:
            if existing != payload.model_dump(mode="json"):
                raise ProjectionConflict("event key already has different content")
            continue
        if isinstance(payload, EvidenceAddedEvent):
            ids = await session.scalars(
                select(EvidenceRow.evidence_id).where(
                    EvidenceRow.task_id == task.task_id,
                    EvidenceRow.evidence_id.in_(payload.evidence_ids),
                )
            )
            if set(ids) != set(payload.evidence_ids):
                raise ProjectionConflict("referenced evidence is missing from the task")
        if isinstance(payload, (ReportReadyEvent, ReportReviewedEvent)):
            report = await session.get(ReportRow, payload.report_id)
            if report is None or report.task_id != task.task_id:
                raise ProjectionConflict("referenced report is missing from the task")
            if isinstance(payload, ReportReadyEvent) and report.version != payload.version:
                raise ProjectionConflict("report version does not match")
            if isinstance(payload, ReportReadyEvent):
                task_map = await session.get(TaskMapRow, task.task_id)
                if task_map is not None:
                    snapshot = MapData.model_validate(
                        {**task_map.data, "report_id": payload.report_id}
                    ).model_dump(mode="json")
                    if report.map_data is not None and report.map_data != snapshot:
                        raise ProjectionConflict("published report map cannot be replaced")
                    report.map_data = snapshot
                await publish_citations(session, report, task.trace_id, payload)
        sequence += 1
        store_event(session, task, sequence, payload, now, projection_key=event.projection_key)
