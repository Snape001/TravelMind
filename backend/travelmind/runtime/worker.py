import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.models import AgentRunRow, ReportRow
from travelmind.db.tasks import append_event, lock_task
from travelmind.research.engine import ResearchEngine
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.runtime.state import ResearchState
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import ErrorInfo
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import (
    AgentFinishedEvent,
    AgentStartedEvent,
    EventPayload,
    ReportReviewedEvent,
    TaskStatusEvent,
)

logger = logging.getLogger(__name__)


class ResearchWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model: StructuredModel,
        engine: ResearchEngine | None,
    ):
        self.sessions = sessions
        retrieve = ResearchRetriever(engine, sessions) if engine is not None else None
        self.graph = build_research_graph(model, retrieve=retrieve)
        self.slots = asyncio.Semaphore(2)

    async def execute(self, task_id: UUID) -> None:
        async with self.slots:
            await self._execute(task_id)

    async def _execute(self, task_id: UUID) -> None:
        async with self.sessions.begin() as session:
            task = await lock_task(session, task_id)
            if task is None or task.status != "queued":
                return
            await append_event(session, task, TaskStatusEvent(type="task.status", status="running"))
            inputs = {
                "task_id": task.task_id,
                "trace_id": task.trace_id,
                "brief": ResearchBrief.model_validate(task.brief),
            }

        state: ResearchState = {}
        terminal = None
        try:
            async for mode, value in self.graph.astream(inputs, stream_mode=["custom", "values"]):
                if mode == "values":
                    state = value
                elif isinstance(value, TaskStatusEvent):
                    if value.status != "running":
                        terminal = value
                else:
                    await self._persist(task_id, value, state)
            if terminal is None:
                raise RuntimeError("research graph ended without a terminal state")
        except Exception as exc:
            logger.error(
                "task.execution_failed task_id=%s trace_id=%s type=%s",
                task_id,
                inputs["trace_id"],
                type(exc).__name__,
            )
            terminal = TaskStatusEvent(
                type="task.status",
                status="failed",
                error=ErrorInfo(
                    code="TASK_EXECUTION_FAILED", message="调研执行中断", retryable=True
                ),
            )
        await self._persist(task_id, terminal, state)

    async def _persist(self, task_id: UUID, payload: EventPayload, state: ResearchState) -> None:
        async with self.sessions.begin() as session:
            task = await lock_task(session, task_id)
            if task is None:
                raise LookupError("research task does not exist")
            now = datetime.now(UTC)
            if isinstance(payload, AgentStartedEvent):
                session.add(
                    AgentRunRow(
                        task_id=task_id,
                        run_id=payload.run_id,
                        agent_id=payload.agent_id,
                        assignment_id=payload.assignment_id,
                        status="running",
                        started_at=now,
                    )
                )
            elif isinstance(payload, AgentFinishedEvent):
                run = await session.get(AgentRunRow, payload.run_id)
                run.status = payload.status
                run.finished_at = now
            elif isinstance(payload, ReportReviewedEvent):
                if state.get("report_id") != payload.report_id:
                    raise ValueError("review event does not match the current report")
                session.add(
                    ReportRow(
                        report_id=payload.report_id,
                        task_id=task_id,
                        version=state.get("review_round", 0) + 1,
                        content=state["draft"].model_dump(mode="json"),
                    )
                )
            elif isinstance(payload, TaskStatusEvent):
                runs = await session.scalars(
                    select(AgentRunRow).where(AgentRunRow.task_id == task_id)
                )
                for run in runs:
                    result = state.get("results", {}).get(run.run_id)
                    if result is not None:
                        run.result = result.model_dump(mode="json")
                    if run.status == "running":
                        run.status = "failed"
                        run.finished_at = now
            await append_event(session, task, payload)
