import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import (
    DEFAULT_MAX_ATTEMPTS,
    ClarificationRow,
    EventRow,
    IntakeRow,
    TaskRow,
)
from travelmind.schemas.api import PendingClarification, TaskRead
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import AgentEvent, EventPayload, TaskStatusEvent
from travelmind.schemas.task_control import next_task_status
from travelmind.schemas.worker import StartResearch


class IdempotencyConflict(Exception):
    pass


async def create_task(
    session: AsyncSession,
    brief: ResearchBrief,
    idempotency_key: str | None,
    *,
    owner_id: UUID,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    from_destination: bool = False,
) -> tuple[TaskRow, bool]:
    body = brief.model_dump(mode="json")
    now = func.clock_timestamp()
    status = next_task_status(None, "create")
    operation = "create_from_destination" if from_destination else "create_task"
    command = (
        insert(TaskRow)
        .values(
            task_id=uuid4(),
            trace_id=uuid4(),
            owner_id=owner_id,
            create_operation=operation,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            brief=body,
            status=status,
            created_at=now,
            updated_at=now,
            available_at=now,
            instruction=StartResearch(kind="start", instruction_id=uuid4(), brief=brief).model_dump(
                mode="json"
            ),
        )
        .on_conflict_do_nothing(constraint="uq_research_tasks_owner_key")
        .returning(TaskRow)
    )
    async with session.begin():
        task = (await session.scalars(command)).one_or_none()
        if task is None:
            existing = (
                await session.scalars(
                    select(TaskRow).where(
                        TaskRow.owner_id == owner_id,
                        TaskRow.create_operation == operation,
                        TaskRow.idempotency_key == idempotency_key,
                    )
                )
            ).one()
            if from_destination:
                intake = await session.get(IntakeRow, existing.task_id)
                matches = intake is not None and intake.original_destination == brief.destination
            else:
                matches = ResearchBrief.model_validate(existing.brief) == brief
            if not matches:
                raise IdempotencyConflict
            return existing, False

        store_event(
            session, task, 1, TaskStatusEvent(type="task.status", status=status), task.created_at
        )
        if from_destination:
            session.add(
                IntakeRow(
                    task_id=task.task_id,
                    original_destination=brief.destination,
                    search_text=brief.destination,
                )
            )
        return task, True


async def lock_task(session: AsyncSession, task_id: UUID) -> TaskRow | None:
    return await session.scalar(
        select(TaskRow)
        .where(TaskRow.task_id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def owned_task(
    session: AsyncSession, task_id: UUID, owner_id: UUID, *, lock: bool = False
) -> TaskRow | None:
    statement = select(TaskRow).where(TaskRow.task_id == task_id, TaskRow.owner_id == owner_id)
    return await session.scalar(statement.with_for_update() if lock else statement)


async def pending_clarification(session: AsyncSession, task_id: UUID) -> ClarificationRow | None:
    return await session.scalar(
        select(ClarificationRow).where(
            ClarificationRow.task_id == task_id, ClarificationRow.answered_at.is_(None)
        )
    )


async def task_read(session: AsyncSession, task: TaskRow) -> TaskRead:
    result = TaskRead.model_validate(task)
    if task.status == "needs_clarification":
        pending = await pending_clarification(session, task.task_id)
        if pending is not None:
            result.pending_clarification = PendingClarification.model_validate(
                {"clarification_id": pending.clarification_id, "questions": pending.questions}
            )
    return result


def payload_digest(payload: dict) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).digest()


async def append_event(
    session: AsyncSession, task: TaskRow, payload: EventPayload, *, now: datetime | None = None
) -> None:
    """The caller holds the task row lock until the event transaction commits."""
    last = await session.scalar(
        select(func.max(EventRow.sequence)).where(EventRow.task_id == task.task_id)
    )
    now = now if now is not None else datetime.now(UTC)
    if isinstance(payload, TaskStatusEvent):
        task.status = payload.status
        task.updated_at = now
    store_event(session, task, (last or 0) + 1, payload, now)


def store_event(
    session: AsyncSession,
    task: TaskRow,
    sequence: int,
    payload: EventPayload,
    now: datetime,
    *,
    projection_key: UUID | None = None,
) -> None:
    event = AgentEvent(
        event_id=uuid4(),
        task_id=task.task_id,
        trace_id=task.trace_id,
        sequence=sequence,
        occurred_at=now,
        payload=payload,
    )
    session.add(
        EventRow(
            **event.model_dump(exclude={"payload"}),
            payload=event.payload.model_dump(mode="json"),
            projection_key=projection_key,
        )
    )


async def events_after(session: AsyncSession, task_id: UUID, cursor: int) -> list[AgentEvent]:
    rows = await session.scalars(
        select(EventRow)
        .where(EventRow.task_id == task_id, EventRow.sequence > cursor)
        .order_by(EventRow.sequence)
        .limit(100)
    )
    return [AgentEvent.model_validate(row, from_attributes=True) for row in rows]
