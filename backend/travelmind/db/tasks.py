from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import EventRow, TaskRow
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import AgentEvent, EventPayload, TaskStatusEvent


class IdempotencyConflict(Exception):
    pass


async def create_task(
    session: AsyncSession, brief: ResearchBrief, idempotency_key: str | None
) -> tuple[TaskRow, bool]:
    body = brief.model_dump(mode="json")
    now = datetime.now(UTC)
    command = (
        insert(TaskRow)
        .values(
            task_id=uuid4(),
            trace_id=uuid4(),
            idempotency_key=idempotency_key,
            brief=body,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[TaskRow.idempotency_key])
        .returning(TaskRow)
    )
    async with session.begin():
        task = (await session.scalars(command)).one_or_none()
        if task is None:
            existing = (
                await session.scalars(
                    select(TaskRow).where(TaskRow.idempotency_key == idempotency_key)
                )
            ).one()
            if existing.brief != body:
                raise IdempotencyConflict
            return existing, False

        store_event(session, task, 1, TaskStatusEvent(type="task.status", status="queued"), now)
        return task, True


async def lock_task(session: AsyncSession, task_id: UUID) -> TaskRow | None:
    return await session.scalar(select(TaskRow).where(TaskRow.task_id == task_id).with_for_update())


async def append_event(session: AsyncSession, task: TaskRow, payload: EventPayload) -> None:
    """The caller holds the task row lock until the event transaction commits."""
    last = await session.scalar(
        select(func.max(EventRow.sequence)).where(EventRow.task_id == task.task_id)
    )
    now = datetime.now(UTC)
    if isinstance(payload, TaskStatusEvent):
        task.status = payload.status
        task.updated_at = now
    store_event(session, task, (last or 0) + 1, payload, now)


def store_event(
    session: AsyncSession, task: TaskRow, sequence: int, payload: EventPayload, now: datetime
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
