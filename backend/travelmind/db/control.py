from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import CommandRow, QuestionnaireRow
from travelmind.db.tasks import (
    IdempotencyConflict,
    append_event,
    owned_task,
    payload_digest,
    pending_clarification,
    task_read,
)
from travelmind.schemas.api import ClarificationAnswers, TaskRead
from travelmind.schemas.events import TaskStatusEvent
from travelmind.schemas.task_control import next_task_status
from travelmind.schemas.worker import AnswerClarification, ResumePause

Operation = Literal["pause", "resume", "answer"]


class TaskNotFound(Exception):
    pass


class ClarificationStale(Exception):
    pass


class AnswerMismatch(Exception):
    pass


async def apply_command(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID,
    operation: Operation,
    key: str,
    *,
    clarification_id: UUID | None = None,
    answers: ClarificationAnswers | None = None,
) -> tuple[int, TaskRead, bool]:
    body = {}
    if answers is not None:
        body = answers.model_dump(mode="json")
        body["answers"].sort(key=lambda answer: answer["question_id"])
        body["clarification_id"] = str(clarification_id)
    digest = payload_digest(body)
    async with session.begin():
        task = await owned_task(session, task_id, owner_id, lock=True)
        if task is None:
            raise TaskNotFound
        scope = (owner_id, task_id, operation, key)
        previous = await session.get(CommandRow, scope)
        if previous is not None:
            if previous.digest != digest:
                raise IdempotencyConflict
            return previous.status_code, TaskRead.model_validate(previous.response), False

        now = await session.scalar(select(func.clock_timestamp()))
        if operation == "pause":
            status = next_task_status(task.status, "request_pause")
            status_code = 200 if status == "paused" else 202
            if status == "paused":
                task.available_at = None
            elif task.pause_requested_at is None:
                task.pause_requested_at = task.updated_at = now
        elif operation == "resume":
            status = next_task_status(task.status, "resume")
            status_code = 202
            if task.paused_interrupt_id is not None:
                task.instruction = ResumePause(
                    kind="resume_pause",
                    instruction_id=uuid4(),
                    interrupt_id=task.paused_interrupt_id,
                ).model_dump(mode="json")
                task.paused_interrupt_id = None
                task.attempt_count = 0
            task.available_at = now
        else:
            pending = await pending_clarification(session, task_id)
            if (
                task.status != "needs_clarification"
                or pending is None
                or pending.clarification_id != clarification_id
            ):
                raise ClarificationStale
            if await session.get(QuestionnaireRow, (task_id, clarification_id)) is not None:
                raise ClarificationStale
            if {item["question_id"] for item in pending.questions} != {
                item["question_id"] for item in body["answers"]
            }:
                raise AnswerMismatch
            status = next_task_status(task.status, "answer")
            status_code = 202
            task.instruction = AnswerClarification(
                kind="answer_clarification",
                instruction_id=uuid4(),
                interrupt_id=pending.interrupt_id,
                clarification_id=pending.clarification_id,
                response=answers,
            ).model_dump(mode="json")
            task.available_at = pending.answered_at = now
            pending.answers = body["answers"]
            task.attempt_count = 0

        if task.status != status:
            await append_event(
                session, task, TaskStatusEvent(type="task.status", status=status), now=now
            )
        result = await task_read(session, task)
        session.add(
            CommandRow(
                owner_id=owner_id,
                task_id=task_id,
                operation=operation,
                key=key,
                digest=digest,
                status_code=status_code,
                response=result.model_dump(mode="json"),
            )
        )
        return status_code, result, True
