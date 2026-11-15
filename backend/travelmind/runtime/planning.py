from typing import Self
from uuid import uuid5

from pydantic import Field, model_validator

from travelmind.runtime.execution import InvalidOutput, result_identity, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.schemas.agents import AgentInput, BriefResult, ClarificationResult, PlanResult
from travelmind.schemas.domain import (
    AgentId,
    Assignment,
    Contract,
    ResearchBrief,
    ResearchPlan,
    Text,
)

DIRECTOR = "research_director"
PLANNER = "planner"
EXPERTS = {
    "history_culture": "历史文化专家，核对地方史、文化遗产、典故和人文背景。",
    "natural_geography": "自然地理专家，核对地形、气候、生态和自然景观。",
    "tourism_resources": "旅游资源专家，评估景点、游览体验、季节性和限制。",
    "social_sentiment": "社交舆情专家，区分游客体验、样本偏差和争议。",
    "travel_planning": "旅游规划专家，评估交通、住宿、费用和行程安排。",
}


class DirectorDecision(Contract):
    brief: ResearchBrief | None = None
    questions: list[Text] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_one_outcome(self) -> Self:
        if (self.brief is not None) == bool(self.questions):
            raise ValueError("return exactly one of brief or questions")
        return self


class ExpertAssignment(Contract):
    agent_id: AgentId
    objective: Text
    depends_on: list[AgentId] = Field(default_factory=list, max_length=len(EXPERTS) - 1)


class PlanDraft(Contract):
    objective: Text
    assignments: list[ExpertAssignment] = Field(min_length=3, max_length=len(EXPERTS))


async def understand(
    model: StructuredModel,
    request: AgentInput,
    clarification_history: list[dict[str, str]] | None = None,
) -> BriefResult | ClarificationResult:
    decision = await with_budget(
        lambda: model(
            request,
            "担任 Research Director。理解中国旅游目的地、调研主题、用户要求和研究深度。"
            "目的地不明确或缺失信息将改变研究方向时，返回最多三个具体问题（如"
            '{"questions":["请问您指哪座古城？"]}）。已明确目的地的通用调研直接开展。'
            "需求可执行时在 brief 中整理研究主题，保留用户的明确要求、限制和 depth，"
            "用 clarification_answers 补充用户需求。不自行补造偏好。"
            "每次只返回 brief 或 questions。",
            DirectorDecision,
            context={"clarification_answers": clarification_history or []},
        ),
        request.budget,
    )
    if decision.brief is None:
        return ClarificationResult(
            **result_identity(request), kind="clarification", questions=decision.questions
        )
    brief = decision.brief.model_copy(
        update={
            "depth": request.brief.depth,
            "requirements": (
                decision.brief.requirements if clarification_history else request.brief.requirements
            ),
        }
    )
    return BriefResult(**result_identity(request), kind="brief", brief=brief)


async def plan_research(
    model: StructuredModel,
    request: AgentInput,
    *,
    focused: bool = False,
    travel_requested: bool | None = None,
) -> PlanResult:
    roster = "\n".join(
        f"- {agent_id}：{role}"
        for agent_id, role in EXPERTS.items()
        if travel_requested is not False or agent_id != "travel_planning"
    )

    async def make_plan() -> ResearchPlan:
        scope = (
            "只围绕 brief 明确请求的范围分配互补核验。不在分工中预设日期、数字或争议；"
            "范围窄时将专家用于独立复核资料及来源关系。\n"
            if focused
            else ""
        )
        draft = await model(
            request,
            "担任 Planner。按主题和深度从以下名册选择 3 至 5 个不同专家，分配互补、可核查的目标。"
            "覆盖用户明确要求。仅在确实需要另一位专家的研究结果时填写 depends_on，"
            "填写已选专家的 agent_id，保证无环，并让可独立运行的任务并行。\n"
            f"{scope}{roster}",
            PlanDraft,
        )
        ids = {task.agent_id: uuid5(request.run_id, task.agent_id) for task in draft.assignments}
        if len(ids) != len(draft.assignments) or ids.keys() - EXPERTS.keys():
            raise InvalidOutput("planner must select distinct experts from the roster")
        if any(set(task.depends_on) - ids.keys() for task in draft.assignments):
            raise InvalidOutput("dependency must reference a selected expert")
        assignments = draft.assignments.copy()
        if travel_requested is False:
            ids.pop("travel_planning", None)
            assignments = [task for task in assignments if task.agent_id in ids]
        if travel_requested and "travel_planning" not in ids:
            ids["travel_planning"] = uuid5(request.run_id, "travel_planning")
            assignments.append(
                ExpertAssignment(
                    agent_id="travel_planning",
                    objective="按已确认目的地和偏好核对景点及交通路线",
                )
            )
        return ResearchPlan(
            objective=draft.objective,
            assignments=[
                Assignment(
                    assignment_id=ids[task.agent_id],
                    agent_id=task.agent_id,
                    objective=task.objective,
                    depends_on=[
                        ids[parent] for parent in dict.fromkeys(task.depends_on) if parent in ids
                    ],
                )
                for task in assignments
            ],
        )

    plan = await with_budget(make_plan, request.budget)
    return PlanResult(**result_identity(request), kind="plan", plan=plan)
