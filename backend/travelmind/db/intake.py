from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.control import ClarificationStale, TaskNotFound
from travelmind.db.models import ClarificationRow, CommandRow, IntakeRow, QuestionnaireRow, TaskRow
from travelmind.db.projections import publish_transition, release_lease
from travelmind.db.tasks import (
    IdempotencyConflict,
    append_event,
    owned_task,
    payload_digest,
    pending_clarification,
    task_read,
)
from travelmind.runtime.intake.questionnaires import travel_preferences
from travelmind.schemas.api import ClarificationQuestion, TaskRead
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.events import ClarificationEvent
from travelmind.schemas.phase4_intake import (
    ChoiceAnswer,
    Destination,
    DestinationTaskCreate,
    QuestionnaireSubmission,
    ResearchIntakeContext,
    ResearchIntakeRead,
    ResearchQuestionnaire,
    TextAnswer,
    TravelPreferences,
)
from travelmind.schemas.task_control import next_task_status
from travelmind.schemas.worker import StartResearch


class InvalidQuestionnaireAnswers(Exception):
    pass


async def read_intake(session: AsyncSession, task: TaskRow) -> ResearchIntakeRead:
    intake = await session.get(IntakeRow, task.task_id)
    active = None
    destination = preferences = None
    if intake:
        if intake.destination:
            destination = Destination.model_validate(intake.destination)
        if intake.planner_context:
            preferences = ResearchIntakeContext.model_validate(
                intake.planner_context
            ).travel_preferences
        if task.status == "needs_clarification":
            pending = await pending_clarification(session, task.task_id)
            if pending:
                active = await session.get(
                    QuestionnaireRow, (task.task_id, pending.clarification_id)
                )
    return ResearchIntakeRead(
        task_id=task.task_id,
        destination=destination,
        travel_preferences=preferences,
        questionnaire=ResearchQuestionnaire.model_validate(active.questionnaire)
        if active
        else None,
    )


async def publish_questionnaire(
    session: AsyncSession, task: TaskRow, questionnaire: ResearchQuestionnaire, now: datetime
) -> None:
    """The caller holds a current execution lease and the task row lock."""
    status = next_task_status(task.status, "clarification_required")
    session.add(
        ClarificationRow(
            task_id=task.task_id,
            clarification_id=questionnaire.questionnaire_id,
            questions=[
                ClarificationQuestion(
                    question_id=uuid5(questionnaire.questionnaire_id, question.question_id),
                    text=question.text,
                ).model_dump(mode="json")
                for question in questionnaire.questions
            ],
            interrupt_id=str(questionnaire.questionnaire_id),
            created_at=now,
        )
    )
    await session.flush()
    session.add(
        QuestionnaireRow(
            task_id=task.task_id,
            questionnaire_id=questionnaire.questionnaire_id,
            questionnaire=questionnaire.model_dump(mode="json"),
        )
    )
    await append_event(
        session,
        task,
        ClarificationEvent(
            type="task.clarification",
            questions=[question.text for question in questionnaire.questions],
        ),
        now=now,
    )
    release_lease(task)
    await publish_transition(session, task, status, now)


async def answered_research(
    session: AsyncSession, task_id: UUID
) -> tuple[ResearchQuestionnaire, QuestionnaireSubmission] | None:
    research = await session.execute(
        select(QuestionnaireRow.questionnaire, ClarificationRow.answers)
        .join(ClarificationRow)
        .where(
            QuestionnaireRow.task_id == task_id,
            QuestionnaireRow.questionnaire["stage"].astext == "research",
            ClarificationRow.answered_at.is_not(None),
        )
    )
    if row := research.one_or_none():
        return (
            ResearchQuestionnaire.model_validate(row.questionnaire),
            QuestionnaireSubmission.model_validate({"answers": row.answers}),
        )
    return None


def _finish_intake(
    task: TaskRow,
    intake: IntakeRow,
    questionnaire: ResearchQuestionnaire,
    submission: QuestionnaireSubmission,
    *,
    requested: bool,
    preferences: TravelPreferences | None = None,
) -> None:
    context = ResearchIntakeContext(
        destination=Destination.model_validate(intake.destination),
        research_questionnaire=questionnaire,
        research_submission=submission,
        travel_route_requested=requested,
        travel_preferences=preferences,
    )
    answers = questionnaire.validate_answers(submission)
    interests = answers.get("interests")
    notes = answers.get("additional_notes")
    selected = set(interests.option_ids) if isinstance(interests, ChoiceAnswer) else set()
    options = next(
        question.options
        for question in questionnaire.questions
        if question.question_id == "interests"
    )
    brief = ResearchBrief(
        destination=context.destination.name,
        depth=task.brief["depth"],
        topics=[option.label for option in options if option.option_id in selected],
        requirements=notes.text if isinstance(notes, TextAnswer) else "",
    )
    intake.planner_context = context.model_dump(mode="json")
    task.brief = brief.model_dump(mode="json")
    task.instruction = StartResearch(
        kind="start",
        instruction_id=uuid5(task.task_id, "intake:start"),
        brief=brief,
    ).model_dump(mode="json")


async def _record_answer(
    session: AsyncSession,
    task: TaskRow,
    intake: IntakeRow,
    questionnaire: ResearchQuestionnaire,
    submission: QuestionnaireSubmission,
) -> None:
    answers = questionnaire.validate_answers(submission)
    if questionnaire.stage == "destination_search":
        answer = answers[questionnaire.questions[0].question_id]
        assert isinstance(answer, TextAnswer)
        intake.search_text = DestinationTaskCreate(destination=answer.text).destination
    elif questionnaire.stage == "destination_confirmation":
        answer = answers[questionnaire.questions[0].question_id]
        assert isinstance(answer, ChoiceAnswer)
        destination = questionnaire.candidates[answer.option_ids[0]]
        intake.destination = destination.model_dump(mode="json")
        task.brief = {**task.brief, "destination": destination.name}
    elif questionnaire.stage == "research":
        interests = answers.get("interests")
        if not isinstance(interests, ChoiceAnswer) or "travel_route" not in interests.option_ids:
            _finish_intake(task, intake, questionnaire, submission, requested=False)
    else:
        research = await answered_research(session, task.task_id)
        if research is None:
            raise RuntimeError("route questionnaire lacks its research source")
        _finish_intake(
            task, intake, *research, requested=True, preferences=travel_preferences(answers)
        )


async def answer_questionnaire(
    session: AsyncSession,
    owner_id: UUID,
    task_id: UUID,
    questionnaire_id: UUID,
    submission: QuestionnaireSubmission,
    key: str,
) -> tuple[TaskRead, bool]:
    body = submission.model_dump(mode="json")
    body["answers"].sort(key=lambda answer: answer["question_id"])
    for answer in body["answers"]:
        if "option_ids" in answer:
            answer["option_ids"].sort()
    digest = payload_digest({**body, "questionnaire_id": str(questionnaire_id)})

    async with session.begin():
        task = await owned_task(session, task_id, owner_id, lock=True)
        if task is None:
            raise TaskNotFound
        scope = (owner_id, task_id, "answer_questionnaire", key)
        if previous := await session.get(CommandRow, scope):
            if previous.digest != digest:
                raise IdempotencyConflict
            return TaskRead.model_validate(previous.response), False

        intake = await session.get(IntakeRow, task_id)
        round_row = await session.get(QuestionnaireRow, (task_id, questionnaire_id))
        pending = await pending_clarification(session, task_id)
        if (
            round_row is None
            or intake is None
            or pending is None
            or pending.clarification_id != questionnaire_id
        ):
            raise ClarificationStale
        status = next_task_status(task.status, "answer")
        questionnaire = ResearchQuestionnaire.model_validate(round_row.questionnaire)
        try:
            await _record_answer(
                session,
                task,
                intake,
                questionnaire,
                QuestionnaireSubmission.model_validate(body),
            )
        except ValueError as exc:
            raise InvalidQuestionnaireAnswers from exc
        now = await session.scalar(select(func.clock_timestamp()))
        pending.answers = body["answers"]
        pending.answered_at = task.available_at = now
        task.attempt_count = 0
        await publish_transition(session, task, status, now)
        result = await task_read(session, task)
        session.add(
            CommandRow(
                owner_id=owner_id,
                task_id=task_id,
                operation="answer_questionnaire",
                key=key,
                digest=digest,
                status_code=202,
                response=result.model_dump(mode="json"),
            )
        )
        return result, True
