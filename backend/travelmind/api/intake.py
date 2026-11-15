import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response

from travelmind.api.dependencies import Sessions, SignedIn, WriteUser, api_error
from travelmind.api.tasks import (
    CONTROL_ERRORS,
    ControlKey,
    _notify_worker,
    not_found,
    task_location,
)
from travelmind.db.control import ClarificationStale, TaskNotFound
from travelmind.db.intake import InvalidQuestionnaireAnswers, answer_questionnaire, read_intake
from travelmind.db.tasks import IdempotencyConflict, create_task, owned_task, task_read
from travelmind.schemas.api import ErrorResponse, IdempotencyKey, TaskRead
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.phase4_intake import (
    DateAnswer,
    DestinationTaskCreate,
    QuestionnaireSubmission,
    ResearchIntakeRead,
)
from travelmind.schemas.task_control import InvalidTaskTransition

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["intake"],
    responses={status: {"model": ErrorResponse} for status in (401, 500, 503)},
)


@router.post(
    "/from-destination",
    status_code=201,
    responses={
        200: {"model": TaskRead, "headers": task_location},
        201: {"headers": task_location},
        **{status: {"model": ErrorResponse} for status in (403, 409, 422)},
    },
)
async def submit_destination(
    body: DestinationTaskCreate,
    request: Request,
    response: Response,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: Annotated[IdempotencyKey | None, Header()] = None,
) -> TaskRead:
    try:
        async with sessions() as session:
            task, created = await create_task(
                session,
                ResearchBrief(destination=body.destination),
                idempotency_key,
                owner_id=user.user_id,
                from_destination=True,
            )
            if created:
                result = TaskRead.model_validate(task)
            else:
                async with session.begin():
                    await session.refresh(task, with_for_update=True)
                    result = await task_read(session, task)
    except IdempotencyConflict:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求") from None
    response.headers["Location"] = f"/api/v1/tasks/{task.task_id}"
    if created:
        logger.info("task.created task_id=%s trace_id=%s", task.task_id, task.trace_id)
        await _notify_worker(request, task.task_id)
    else:
        response.status_code = 200
    return result


@router.get("/{task_id}/intake", responses=not_found)
async def get_intake(task_id: UUID, sessions: Sessions, user: SignedIn) -> ResearchIntakeRead:
    async with sessions.begin() as session:
        task = await owned_task(session, task_id, user.user_id, lock=True)
        if task is None:
            raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在")
        return await read_intake(session, task)


@router.post(
    "/{task_id}/questionnaires/{questionnaire_id}/answer",
    status_code=202,
    responses=CONTROL_ERRORS,
)
async def submit_questionnaire(
    task_id: UUID,
    questionnaire_id: UUID,
    body: QuestionnaireSubmission,
    request: Request,
    sessions: Sessions,
    user: WriteUser,
    idempotency_key: ControlKey,
) -> TaskRead:
    try:
        if any(isinstance(answer, DateAnswer) for answer in body.answers):
            original = (await request.json())["answers"]
            for raw, answer in zip(original, body.answers, strict=True):
                if isinstance(answer, DateAnswer) and raw["value"] != answer.value.isoformat():
                    raise InvalidQuestionnaireAnswers
        async with sessions() as session:
            result, applied = await answer_questionnaire(
                session, user.user_id, task_id, questionnaire_id, body, idempotency_key
            )
    except TaskNotFound:
        raise api_error(404, "TASK_NOT_FOUND", "调研任务不存在") from None
    except ClarificationStale:
        raise api_error(409, "CLARIFICATION_STALE", "已不是当前待答轮次") from None
    except IdempotencyConflict:
        raise api_error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求") from None
    except InvalidTaskTransition:
        raise api_error(409, "TASK_STATE_CONFLICT", "任务状态不支持该操作") from None
    except InvalidQuestionnaireAnswers:
        raise api_error(
            422, "INVALID_QUESTIONNAIRE_ANSWERS", "答复不符合当前问卷，请核对选项或日期"
        ) from None
    if applied:
        await _notify_worker(request, task_id)
    return result
