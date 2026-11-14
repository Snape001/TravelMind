import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.models import EvidenceRow, ReportRow, TaskRow
from travelmind.db.tasks import IdempotencyConflict, create_task, events_after
from travelmind.schemas.api import ErrorResponse, EvidenceRead, ReportRead, TaskRead
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import AgentEvent, EventCursor

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["tasks"],
    responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
not_found = {404: {"model": ErrorResponse}}
task_location = {"Location": {"description": "Task resource URL", "schema": {"type": "string"}}}
sse_error_content = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}


def api_error(status: int, code: str, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, "retryable": retryable})


def get_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions = request.app.state.sessions
    if sessions is None:
        raise api_error(503, "DATABASE_UNAVAILABLE", "任务数据库不可用", retryable=True)
    return sessions


Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessions)]


async def require_task(task_id: UUID, sessions: Sessions) -> TaskRow:
    async with sessions() as session:
        task = await session.get(TaskRow, task_id)
    if task is None:
        raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在")
    return task


ExistingTask = Annotated[TaskRow, Depends(require_task)]


@router.post(
    "",
    status_code=201,
    responses={
        200: {"model": TaskRead, "headers": task_location},
        201: {"headers": task_location},
        409: {"model": ErrorResponse},
    },
)
async def submit_task(
    brief: ResearchBrief,
    response: Response,
    sessions: Sessions,
    idempotency_key: Annotated[
        str | None, Header(min_length=1, max_length=128, pattern=r"^[!-~]+$")
    ] = None,
) -> TaskRead:
    try:
        async with sessions() as session:
            task, created = await create_task(session, brief, idempotency_key)
    except IdempotencyConflict as exc:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求") from exc
    response.headers["Location"] = f"/api/v1/tasks/{task.task_id}"
    if created:
        logger.info("task.created task_id=%s trace_id=%s", task.task_id, task.trace_id)
    else:
        response.status_code = 200
    return TaskRead.model_validate(task)


@router.get("/{task_id}", responses=not_found)
async def read_task(task: ExistingTask) -> TaskRead:
    return TaskRead.model_validate(task)


@router.get(
    "/{task_id}/events",
    response_class=EventSourceResponse,
    responses={
        200: {"model": AgentEvent},
        404: {"content": sse_error_content},
        500: {"content": sse_error_content},
        503: {"content": sse_error_content},
    },
)
async def stream_events(
    task: ExistingTask,
    sessions: Sessions,
    after: EventCursor = 0,
    last_event_id: Annotated[EventCursor | None, Header()] = None,
) -> AsyncIterator[ServerSentEvent]:
    cursor = last_event_id if last_event_id is not None else after
    while True:
        try:
            async with sessions() as session:
                events = await events_after(session, task.task_id, cursor)
        except (OSError, SQLAlchemyError) as exc:
            logger.warning(
                "task.stream_interrupted task_id=%s trace_id=%s type=%s",
                task.task_id,
                task.trace_id,
                type(exc).__name__,
            )
            return
        for event in events:
            cursor = event.sequence
            yield ServerSentEvent(
                id=str(cursor),
                event=event.payload.type,
                raw_data=event.model_dump_json(),
            )
        if not events:
            await asyncio.sleep(1)


@router.get("/{task_id}/evidence/{evidence_id}", responses=not_found)
async def read_evidence(task: ExistingTask, evidence_id: UUID, sessions: Sessions) -> EvidenceRead:
    async with sessions() as session:
        evidence = await session.scalar(
            select(EvidenceRow).where(
                EvidenceRow.task_id == task.task_id, EvidenceRow.evidence_id == evidence_id
            )
        )
    if evidence is None:
        raise api_error(404, "EVIDENCE_NOT_FOUND", "未找到该任务的证据")
    return EvidenceRead.model_validate(evidence)


@router.get("/{task_id}/reports/{report_id}", responses=not_found)
async def read_report(task: ExistingTask, report_id: UUID, sessions: Sessions) -> ReportRead:
    async with sessions() as session:
        report = await session.scalar(
            select(ReportRow).where(
                ReportRow.task_id == task.task_id, ReportRow.report_id == report_id
            )
        )
    if report is None:
        raise api_error(404, "REPORT_NOT_FOUND", "未找到该任务的报告")
    return ReportRead.model_validate(report)
