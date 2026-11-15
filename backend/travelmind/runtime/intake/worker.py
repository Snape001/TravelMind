from typing import TypeVar
from uuid import uuid5

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.intake import answered_research, publish_questionnaire
from travelmind.db.models import IntakeRow, TaskRow
from travelmind.db.projections import publish_transition, release_lease
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.amap_poi import AmapPoi
from travelmind.runtime.execution import RunError
from travelmind.runtime.intake.questionnaires import (
    build_destination_questionnaire,
    build_research_questionnaire,
    build_travel_questionnaire,
)
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.planning import DIRECTOR, PLANNER, DirectorDecision
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.phase4_intake import Destination, ResearchIntakeContext
from travelmind.schemas.task_control import next_task_status
from travelmind.schemas.worker import AttemptFailure, TaskClaim

Response = TypeVar("Response", bound=BaseModel)


class IntakeWorker:
    """Publish intake rounds before the initial research checkpoint is created."""

    def __init__(self, store: PostgresWorkerStore, poi: AmapPoi, model: StructuredModel):
        self.store = store
        self.poi = poi
        self.model = model

    async def prepare(self, claim: TaskClaim) -> tuple[bool, ResearchIntakeContext | None]:
        lease = claim.lease
        async with self.store.sessions.begin() as session:
            intake = await session.get(IntakeRow, lease.task_id)
            if intake is None:
                return True, None
            if intake.planner_context is not None:
                return True, ResearchIntakeContext.model_validate(intake.planner_context)
            if claim.execution_permitted:
                await self.store.check_lease(lease, transaction=session)
                task = await session.get(TaskRow, lease.task_id)
                if await _pause_if_requested(session, task):
                    return False, None
                brief = ResearchBrief.model_validate(task.brief)
                search_text = intake.search_text
                destination = (
                    Destination.model_validate(intake.destination) if intake.destination else None
                )
                travel = (
                    destination is not None
                    and await answered_research(session, lease.task_id) is not None
                )

        if not claim.execution_permitted:
            await self._failure(claim, RunError("ATTEMPT_BUDGET_EXHAUSTED", "任务执行次数已达上限"))
            return False, None

        try:
            if destination is None:
                questionnaire = build_destination_questionnaire(await self.poi.search(search_text))
            elif travel:
                questionnaire = build_travel_questionnaire()
            else:
                questionnaire = await build_research_questionnaire(
                    self.model, lease.task_id, claim.trace_id, brief, destination
                )
        except RunError as error:
            async with self.store.sessions.begin() as session:
                await self.store.check_lease(lease, transaction=session)
                task = await session.get(TaskRow, lease.task_id)
                if await _pause_if_requested(session, task):
                    return False, None
            await self._failure(claim, error)
            return False, None

        async with self.store.sessions.begin() as session:
            await self.store.check_lease(lease, transaction=session)
            task = await session.get(TaskRow, lease.task_id)
            if not await _pause_if_requested(session, task):
                now = await session.scalar(select(func.clock_timestamp()))
                await publish_questionnaire(session, task, questionnaire, now)
        return False, None

    async def _failure(self, claim: TaskClaim, error: RunError) -> None:
        info = error.info.model_copy(
            update={"retryable": error.info.retryable and claim.attempt_count < claim.max_attempts}
        )
        await self.store.commit_projection(
            claim.lease,
            AttemptFailure(
                failure_id=uuid5(claim.lease.task_id, f"attempt:{claim.lease.lease_epoch}"),
                error=info,
            ),
        )


async def _pause_if_requested(session: AsyncSession, task: TaskRow) -> bool:
    if task.pause_requested_at is None:
        return False
    status = next_task_status(task.status, "pause_reached")
    task.pause_requested_at = None
    task.attempt_count = 0
    release_lease(task)
    await publish_transition(
        session, task, status, await session.scalar(select(func.clock_timestamp()))
    )
    return True


def model_with_intake(model: StructuredModel, intake: ResearchIntakeContext) -> StructuredModel:
    snapshot = intake.model_dump(mode="json")

    async def invoke(
        request: AgentInput,
        instructions: str,
        response_type: type[Response],
        /,
        *,
        context: dict | None = None,
    ) -> Response:
        if request.agent_id == DIRECTOR and response_type is DirectorDecision:
            # The typed rounds already completed Director clarification before the graph.
            return response_type.model_validate({"brief": request.brief})
        if request.agent_id == PLANNER:
            instructions += (
                "\nresearch_intake 是已确认的目的地、研究问卷及答复，"
                "请用行政区和地址区分同名地点，按答复安排研究分工。"
                "若 travel_route_requested 为真，安排 travel_planning，并使用已填写的出行偏好；"
                "空值代表未填写。若为假，只研究用户已选方向或提供目的地综合调研。"
            )
            context = {**(context or {}), "research_intake": snapshot}
        return await model(request, instructions, response_type, context=context)

    return invoke
