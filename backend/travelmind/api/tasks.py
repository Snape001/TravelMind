import asyncio
import logging
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from travelmind.api.dependencies import Sessions, SignedIn, WriteUser, api_error
from travelmind.config import WAKE_CHANNEL
from travelmind.db.auth import session_is_active
from travelmind.db.control import (
    AnswerMismatch,
    ClarificationStale,
    Operation,
    TaskNotFound,
    apply_command,
)
from travelmind.db.models import EvidenceRow, ReportRow, TaskMapRow, TaskRow
from travelmind.db.tasks import (
    IdempotencyConflict,
    create_task,
    events_after,
    owned_task,
    task_read,
)
from travelmind.schemas.api import (
    ClarificationAnswers,
    ErrorResponse,
    EvidenceRead,
    IdempotencyKey,
    ReportRead,
    TaskRead,
)
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import AgentEvent, EventCursor
from travelmind.schemas.map_data import MapData, MapIssue
from travelmind.schemas.phase4_sources import TravelRoutesRead
from travelmind.schemas.task_control import InvalidTaskTransition

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["tasks"],
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
not_found = {404: {"model": ErrorResponse}}
task_location = {"Location": {"description": "Task resource URL", "schema": {"type": "string"}}}
sse_error_content = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
SSE_SESSION_CHECK_SECONDS = 5
CONTROL_ERRORS = {status: {"model": ErrorResponse} for status in (403, 404, 409, 422)}
ControlKey = Annotated[IdempotencyKey, Header()]


async def require_task(task_id: UUID, sessions: Sessions, user: SignedIn) -> TaskRow:
    async with sessions() as session:
        task = await owned_task(session, task_id, user.user_id)
    if task is None:
        raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在")
    return task


ExistingTask = Annotated[TaskRow, Depends(require_task)]


async def _notify_worker(request: Request, task_id: UUID) -> None:
    redis = request.app.state.redis
    if redis is None:
        return
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            await redis.publish(WAKE_CHANNEL, str(task_id))
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning("task.wakeup_failed task_id=%s type=%s", task_id, type(exc).__name__)


async def _require_empty_body(request: Request, message: str) -> None:
    async for chunk in request.stream():
        if chunk:
            raise api_error(422, "VALIDATION_ERROR", message)


@router.post(
    "",
    status_code=201,
    responses={
        200: {"model": TaskRead, "headers": task_location},
        201: {"headers": task_location},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def submit_task(
    brief: ResearchBrief,
    request: Request,
    response: Response,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: Annotated[IdempotencyKey | None, Header()] = None,
) -> TaskRead:
    try:
        async with sessions() as session:
            task, created = await create_task(
                session, brief, idempotency_key, owner_id=user.user_id
            )
            if created:
                result = TaskRead.model_validate(task)
            else:
                async with session.begin():
                    await session.refresh(task, with_for_update=True)
                    result = await task_read(session, task)
    except IdempotencyConflict as exc:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求") from exc
    response.headers["Location"] = f"/api/v1/tasks/{task.task_id}"
    if created:
        logger.info("task.created task_id=%s trace_id=%s", task.task_id, task.trace_id)
        await _notify_worker(request, task.task_id)
    else:
        response.status_code = 200
    return result


@router.get("/{task_id}", responses=not_found)
async def read_task(task_id: UUID, sessions: Sessions, user: SignedIn) -> TaskRead:
    async with sessions.begin() as session:
        task = await owned_task(session, task_id, user.user_id, lock=True)
        if task is None:
            raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在")
        return await task_read(session, task)


async def _control(
    task_id: UUID,
    session_factory: Sessions,
    user: WriteUser,
    response: Response,
    operation: Operation,
    key: str,
    *,
    clarification_id: UUID | None = None,
    answers: ClarificationAnswers | None = None,
) -> tuple[TaskRead, bool]:
    try:
        async with session_factory() as session:
            status, result, applied = await apply_command(
                session,
                user.user_id,
                task_id,
                operation,
                key,
                clarification_id=clarification_id,
                answers=answers,
            )
    except TaskNotFound:
        raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在") from None
    except IdempotencyConflict:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求") from None
    except InvalidTaskTransition:
        raise api_error(409, "TASK_STATE_CONFLICT", "任务状态不支持该操作") from None
    except ClarificationStale:
        raise api_error(409, "CLARIFICATION_STALE", "已不是当前待答轮次") from None
    except AnswerMismatch:
        raise api_error(422, "VALIDATION_ERROR", "必须为当前轮次的每个问题提供一个答案") from None
    response.status_code = status
    return result, applied


@router.post("/{task_id}/pause", responses={202: {"model": TaskRead}, **CONTROL_ERRORS})
async def pause_task(
    task: ExistingTask,
    request: Request,
    response: Response,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: ControlKey,
) -> TaskRead:
    await _require_empty_body(request, "暂停请求不接受请求体")
    result, _ = await _control(task.task_id, sessions, user, response, "pause", idempotency_key)
    return result


@router.post("/{task_id}/resume", status_code=202, responses=CONTROL_ERRORS)
async def resume_task(
    task: ExistingTask,
    request: Request,
    response: Response,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: ControlKey,
) -> TaskRead:
    await _require_empty_body(request, "恢复请求不接受请求体")
    result, applied = await _control(
        task.task_id, sessions, user, response, "resume", idempotency_key
    )
    if applied:
        await _notify_worker(request, task.task_id)
    return result


@router.post(
    "/{task_id}/clarifications/{clarification_id}/answer",
    status_code=202,
    responses=CONTROL_ERRORS,
)
async def answer_task(
    task: ExistingTask,
    clarification_id: UUID,
    answers: ClarificationAnswers,
    request: Request,
    response: Response,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: ControlKey,
) -> TaskRead:
    result, applied = await _control(
        task.task_id,
        sessions,
        user,
        response,
        "answer",
        idempotency_key,
        clarification_id=clarification_id,
        answers=answers,
    )
    if applied:
        await _notify_worker(request, task.task_id)
    return result


@router.get(
    "/{task_id}/events",
    response_class=EventSourceResponse,
    responses={
        200: {"model": AgentEvent},
        401: {"content": sse_error_content},
        404: {"content": sse_error_content},
        500: {"content": sse_error_content},
        503: {"content": sse_error_content},
    },
)
async def stream_events(
    task: ExistingTask,
    sessions: Sessions,
    user: SignedIn,
    after: EventCursor = 0,
    last_event_id: Annotated[EventCursor | None, Header()] = None,
) -> AsyncIterator[ServerSentEvent]:
    cursor = last_event_id if last_event_id is not None else after
    check_at = 0.0
    while True:
        try:
            async with sessions() as session:
                if monotonic() >= check_at:
                    if not await session_is_active(session, user):
                        return
                    check_at = monotonic() + SSE_SESSION_CHECK_SECONDS
                events = await events_after(session, task.task_id, cursor)
                if not events:
                    status = await session.scalar(
                        select(TaskRow.status).where(TaskRow.task_id == task.task_id)
                    )
                    if status is None:
                        return
                    if status in ("completed", "failed"):
                        # Recheck the event cursor after observing a committed terminal state.
                        events = await events_after(session, task.task_id, cursor)
                        if not events:
                            return
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
            if event.payload.type == "task.status" and event.payload.status in (
                "completed",
                "failed",
            ):
                return
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


@router.get("/{task_id}/routes", responses=not_found)
async def read_routes(task: ExistingTask, sessions: Sessions) -> TravelRoutesRead:
    async with sessions() as session:
        row = await session.get(TaskMapRow, task.task_id)
    routes = MapData.model_validate(row.data).selected_routes() if row is not None else []
    return TravelRoutesRead(task_id=task.task_id, routes=routes)


@router.get("/{task_id}/reports/{report_id}/map-data", responses=not_found)
async def read_report_map(task: ExistingTask, report_id: UUID, sessions: Sessions) -> MapData:
    async with sessions() as session:
        report = await session.scalar(
            select(ReportRow).where(
                ReportRow.task_id == task.task_id, ReportRow.report_id == report_id
            )
        )
    if report is None:
        raise api_error(404, "REPORT_NOT_FOUND", "未找到该任务的报告")
    if report.map_data is not None:
        return MapData.model_validate(report.map_data)
    return MapData(
        task_id=task.task_id,
        report_id=report_id,
        status="unavailable",
        issues=[MapIssue(code="MAP_DATA_UNAVAILABLE", message="该报告没有可用地图数据")],
    )
