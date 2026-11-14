from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import Interval, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from travelmind.db.models import DEFAULT_MAX_ATTEMPTS, AttemptFailureRow, ProjectionRow, TaskRow
from travelmind.db.projections import apply_batch, publish_transition, release_lease
from travelmind.db.tasks import lock_task, payload_digest
from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.task_control import next_task_status
from travelmind.schemas.worker import (
    AttemptFailure,
    ExecutionInstruction,
    LeaseLost,
    LeaseRef,
    ProjectionBatch,
    ProjectionConflict,
    TaskClaim,
    WorkerInstruction,
)

INSTRUCTION = TypeAdapter(ExecutionInstruction)
ATTEMPT_BUDGET_EXHAUSTED = ErrorInfo(
    code="ATTEMPT_BUDGET_EXHAUSTED", message="任务执行次数已达上限", retryable=False
)
BACKOFF_CAP_SECONDS = 30
BACKOFF_CAP_EXPONENT = 5


class TransactionStatus(Protocol):
    name: str


class TransactionInfo(Protocol):
    @property
    def transaction_status(self) -> TransactionStatus: ...


class Cursor(Protocol):
    async def fetchone(self) -> object | None: ...


class CheckpointTransaction(Protocol):
    # psycopg AsyncConnection, already inside the transaction used for the checkpoint write.
    @property
    def info(self) -> TransactionInfo: ...

    async def execute(self, query: str, params: tuple[object, ...]) -> Cursor: ...


def _matches(row, lease: LeaseRef, now: datetime) -> bool:
    return (
        row is not None
        and row.status == "running"
        and row.lease_worker_id == lease.worker_id
        and row.lease_epoch == lease.lease_epoch
        and row.lease_until is not None
        and row.lease_until > now
    )


class PostgresWorkerStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ):
        # Keep accepting the legacy constructor argument. New limits belong to create_task.
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.sessions = sessions

    async def claim(self, worker_id: UUID, *, lease_seconds: int) -> TaskClaim | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self.sessions.begin() as session:
            due = func.statement_timestamp()
            budget_available = TaskRow.attempt_count < TaskRow.max_attempts
            backoff = func.least(
                func.power(2, func.least(TaskRow.attempt_count, BACKOFF_CAP_EXPONENT)),
                BACKOFF_CAP_SECONDS,
            ) * literal(timedelta(seconds=1), type_=Interval())
            priority = case(
                (TaskRow.status == "queued", TaskRow.available_at), else_=TaskRow.lease_until
            )
            task = await session.scalar(
                select(TaskRow)
                .where(
                    TaskRow.owner_id.is_not(None),
                    or_(
                        and_(
                            TaskRow.status == "queued",
                            TaskRow.available_at <= due,
                            budget_available,
                        ),
                        and_(
                            TaskRow.status == "running",
                            TaskRow.lease_until <= due,
                            or_(~budget_available, TaskRow.lease_until + backoff <= due),
                        ),
                    ),
                )
                .order_by(priority, TaskRow.created_at, TaskRow.task_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            reclaimed = task.status == "running"
            task.available_at = None
            task.lease_worker_id = worker_id
            task.lease_until = now + timedelta(seconds=lease_seconds)
            task.lease_epoch += 1
            task.lease_execution_permitted = task.attempt_count < task.max_attempts
            if task.lease_execution_permitted:
                task.attempt_count += 1
            if not reclaimed:
                await publish_transition(session, task, next_task_status(task.status, "claim"), now)
            return TaskClaim(
                lease=LeaseRef(
                    task_id=task.task_id, worker_id=worker_id, lease_epoch=task.lease_epoch
                ),
                trace_id=task.trace_id,
                lease_until=task.lease_until,
                reclaimed=reclaimed,
                max_attempts=task.max_attempts,
                attempt_count=task.attempt_count,
                execution_permitted=task.lease_execution_permitted,
            )

    async def _locked_lease(
        self, session: AsyncSession, lease: LeaseRef
    ) -> tuple[TaskRow, datetime]:
        if not session.in_transaction():
            raise ValueError("lease fencing requires an open transaction")
        task = await lock_task(session, lease.task_id)
        now = await session.scalar(select(func.clock_timestamp()))
        if not _matches(task, lease, now):
            raise LeaseLost
        return task, now

    async def check_lease(
        self,
        lease: LeaseRef,
        *,
        transaction: AsyncSession | AsyncConnection | CheckpointTransaction,
    ) -> None:
        if isinstance(transaction, AsyncSession):
            task, _ = await self._locked_lease(transaction, lease)
            if not task.lease_execution_permitted:
                raise LeaseLost
            return
        if isinstance(transaction, AsyncConnection):
            if not transaction.in_transaction():
                raise ValueError("lease fencing requires an open transaction")
            table = TaskRow.__table__
            row = (
                await transaction.execute(
                    select(
                        table.c.status,
                        table.c.lease_worker_id,
                        table.c.lease_epoch,
                        table.c.lease_until,
                        table.c.lease_execution_permitted,
                    )
                    .where(table.c.task_id == lease.task_id)
                    .with_for_update()
                )
            ).one_or_none()
            now = await transaction.scalar(select(func.clock_timestamp()))
            if not _matches(row, lease, now) or not row.lease_execution_permitted:
                raise LeaseLost
            return
        # A psycopg connection is also used by AsyncPostgresSaver. Check transaction state
        # before SELECT so an autocommit connection cannot silently release the row lock.
        if transaction.info.transaction_status.name != "INTRANS":
            raise ValueError("lease fencing requires an open transaction")
        lock = await transaction.execute(
            "SELECT task_id FROM research_tasks WHERE task_id = %s FOR UPDATE", (lease.task_id,)
        )
        if await lock.fetchone() is None:
            raise LeaseLost
        match = await transaction.execute(
            """SELECT 1 FROM research_tasks WHERE task_id = %s AND status = 'running'
                AND lease_worker_id = %s AND lease_epoch = %s
                AND lease_execution_permitted IS TRUE
                AND lease_until > clock_timestamp()""",
            (lease.task_id, lease.worker_id, lease.lease_epoch),
        )
        if await match.fetchone() is None:
            raise LeaseLost

    async def renew(self, lease: LeaseRef, *, lease_seconds: int) -> datetime:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        async with self.sessions.begin() as session:
            task, now = await self._locked_lease(session, lease)
            task.lease_until = now + timedelta(seconds=lease_seconds)
            return task.lease_until

    async def load_instruction(self, lease: LeaseRef) -> WorkerInstruction:
        async with self.sessions.begin() as session:
            task, _ = await self._locked_lease(session, lease)
            return WorkerInstruction(
                instruction=INSTRUCTION.validate_python(task.instruction),
                pause_requested_at=task.pause_requested_at,
                projected_index=task.projected_index,
            )

    async def commit_projection(
        self, lease: LeaseRef, projection: ProjectionBatch | AttemptFailure
    ) -> Literal["applied", "already_applied"]:
        digest = payload_digest(projection.model_dump(mode="json"))
        async with self.sessions.begin() as session:
            task, now = await self._locked_lease(session, lease)
            if isinstance(projection, AttemptFailure):
                prior = await session.get(AttemptFailureRow, (task.task_id, projection.failure_id))
                if prior is not None:
                    if prior.digest != digest:
                        raise ProjectionConflict("failure ID was reused with different content")
                    return "already_applied"
                session.add(
                    AttemptFailureRow(
                        task_id=task.task_id,
                        failure_id=projection.failure_id,
                        digest=digest,
                    )
                )
                error = projection.error
                if error.retryable and task.attempt_count >= task.max_attempts:
                    error = ATTEMPT_BUDGET_EXHAUSTED
                retry = error.retryable
                status = next_task_status(task.status, "retry" if retry else "fail")
                release_lease(task)
                if retry:
                    task.available_at = now + timedelta(
                        seconds=min(
                            2 ** min(task.attempt_count, BACKOFF_CAP_EXPONENT), BACKOFF_CAP_SECONDS
                        )
                    )
                else:
                    task.pause_requested_at = None
                await publish_transition(session, task, status, now, error=error)
                return "applied"

            index = projection.projection_index
            if index <= task.projected_index:
                prior = await session.get(ProjectionRow, (task.task_id, index))
                if (
                    prior is None
                    or prior.checkpoint_id != projection.checkpoint_id
                    or prior.digest != digest
                ):
                    raise ProjectionConflict("projection replay differs from committed content")
                return "already_applied"
            if index != task.projected_index + 1:
                raise ProjectionConflict("projection index is not the next one")
            await apply_batch(session, task, projection, now)
            task.projected_index = index
            session.add(
                ProjectionRow(
                    task_id=task.task_id,
                    projection_index=index,
                    checkpoint_id=projection.checkpoint_id,
                    digest=digest,
                )
            )
            return "applied"
