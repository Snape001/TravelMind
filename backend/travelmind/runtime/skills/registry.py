import json
import logging
import re
from collections.abc import Callable
from importlib.resources import files
from uuid import uuid4

from pydantic import BaseModel

from travelmind.runtime.execution import RunError, with_budget
from travelmind.runtime.model import Response, StructuredModel
from travelmind.runtime.skills.contracts import SkillDefinition
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.phase4_experts import (
    ExpertProfilesRead,
    ExpertSkillInvocation,
    ExpertSkillResult,
    SkillRef,
)

logger = logging.getLogger(__name__)

# The index contains stable references only. Individual definitions are read on selection.
RESEARCH_SKILLS = {
    "history_culture": "historical_interpretation",
    "natural_geography": "landscape_and_season",
    "tourism_resources": "attraction_assessment",
    "social_sentiment": "visitor_feedback",
    "literature_research": "literature_verification",
    "internet_research": "web_source_verification",
}
SKILL_IDS = {
    **{(agent, "research"): skill for agent, skill in RESEARCH_SKILLS.items()},
    ("research_director", "research"): "research_scoping",
    ("travel_planning", "plan_route"): "itinerary_assessment",
    ("report_writer", "outline"): "report_architecture",
    ("report_writer", "synthesize"): "evidence_synthesis",
    ("report_writer", "write"): "editorial_writing",
    ("report_writer", "edit"): "travel_value_orientation",
    ("reviewer", "review"): "independent_report_review",
}


def read_definition(ref: SkillRef) -> SkillDefinition:
    path = files(__package__).joinpath("definitions", f"{ref.skill_id}.v{ref.version}.json")
    return SkillDefinition.model_validate_json(path.read_text(encoding="utf-8"))


def profile_id(agent_id: str) -> str:
    return "report_writer" if agent_id == "writer" else agent_id


def schema_key(schema: type[BaseModel]) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()


class SkillRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, int], SkillDefinition] = {}

    def select(self, agent_id: str, stage: str) -> SkillDefinition:
        role = profile_id(agent_id)
        skill_id = SKILL_IDS.get((role, stage))
        if skill_id is None:
            raise RunError("SKILL_NOT_AVAILABLE", "当前角色没有适用于此阶段的技能")
        ref = SkillRef(skill_id=skill_id, version=1)
        key = (ref.skill_id, ref.version)
        if key not in self._definitions:
            skill = read_definition(ref)
            if (
                (skill.skill_id, skill.version) != key
                or role not in skill.agent_ids
                or skill.stage != stage
                or bool(skill.allowed_tools) != bool(skill.budget.tool_calls)
            ):
                raise RunError("SKILL_CONFIGURATION", "技能定义与注册表不一致")
            self._definitions[key] = skill
        return self._definitions[key].model_copy(deep=True)

    def profiles(self) -> ExpertProfilesRead:
        path = files("travelmind.expert_profiles").joinpath("profiles.json")
        profiles = ExpertProfilesRead.model_validate_json(path.read_text(encoding="utf-8"))
        expected = {
            role: {skill for (owner, _), skill in SKILL_IDS.items() if owner == role}
            for role, _ in SKILL_IDS
        }
        ids = [profile.agent_id for profile in profiles.experts]
        if len(ids) != len(set(ids)) or set(ids) != expected.keys():
            raise RunError("SKILL_CONFIGURATION", "专家档案与技能注册表不一致")
        for profile in profiles.experts:
            refs = {(ref.skill_id, ref.version) for ref in profile.skills}
            if len(refs) != len(profile.skills) or refs != {
                (skill_id, 1) for skill_id in expected[profile.agent_id]
            }:
                raise RunError("SKILL_CONFIGURATION", "专家档案引用了未注册的技能")
        return profiles

    def session(self, request: AgentInput, stage: str) -> "SkillSession":
        return SkillSession(request, self.select(request.agent_id, stage))


class SkillSession:
    def __init__(self, request: AgentInput, skill: SkillDefinition) -> None:
        self.skill = skill
        self.invocation_id = uuid4()
        self.remaining_tools = skill.budget.tool_calls
        self._started = False
        model_budget = skill.budget.model
        budget = RunBudget(
            timeout_seconds=min(model_budget.timeout_seconds, request.budget.timeout_seconds),
            max_attempts=min(model_budget.max_attempts, request.budget.max_attempts),
            max_output_tokens=min(model_budget.max_output_tokens, request.budget.max_output_tokens),
        )
        self.request = request.model_copy(update={"budget": budget})

    def take_tool(self, name: str, task_tools: set[str]) -> None:
        if name not in self.skill.allowed_tools or name not in task_tools:
            raise RunError("SKILL_TOOL_DENIED", "当前技能或任务无权调用该工具")
        if self.remaining_tools == 0:
            raise RunError("SKILL_TOOL_BUDGET", "技能工具调用次数已用完")
        self.remaining_tools -= 1

    async def run(
        self,
        model: StructuredModel,
        data: BaseModel,
        output_schema: type[Response],
        *,
        validate: Callable[[Response], None] | None = None,
    ) -> Response:
        skill = self.skill
        if self._started:
            raise RunError("SKILL_ALREADY_STARTED", "同一次技能调用只能执行一次")
        self._started = True
        if (
            schema_key(type(data)) != skill.input_schema
            or schema_key(output_schema) != skill.output_schema
        ):
            raise RunError("SKILL_CONFIGURATION", "技能输入或输出 Schema 未匹配")
        request = self.request
        data = type(data).model_validate(data.model_dump(mode="json"))
        invocation = ExpertSkillInvocation(
            invocation_id=self.invocation_id,
            skill=SkillRef(skill_id=skill.skill_id, version=skill.version),
            task_id=request.task_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            input=data.model_dump(mode="json"),
        )
        instructions = "\n".join(
            [skill.title, "执行步骤", *skill.steps, "质量要求", *skill.quality_requirements]
        )
        payload = [
            request.model_dump(mode="json", exclude_none=True),
            invocation.input,
            output_schema.model_json_schema(),
            instructions,
        ]
        if len(json.dumps(payload, ensure_ascii=False)) > skill.max_context_chars:
            raise RunError("SKILL_CONTEXT_BUDGET", "技能输入超出上下文预算")

        async def call() -> Response:
            response = await model(request, instructions, output_schema, context=invocation.input)
            output = output_schema.model_validate(response.model_dump(mode="json"))
            if validate is not None:
                validate(output)
            return output

        try:
            output = await with_budget(call, request.budget)
        except RunError as exc:
            result = ExpertSkillResult(
                invocation_id=invocation.invocation_id, status="failed", error=exc.info
            )
            self._log(result)
            raise
        result = ExpertSkillResult(
            invocation_id=invocation.invocation_id,
            status="completed",
            output=output.model_dump(mode="json"),
        )
        self._log(result)
        return output

    def _log(self, result: ExpertSkillResult) -> None:
        logger.info(
            "skill.finished task_id=%s trace_id=%s run_id=%s agent_id=%s "
            "invocation_id=%s skill_id=%s version=%s status=%s",
            self.request.task_id,
            self.request.trace_id,
            self.request.run_id,
            self.request.agent_id,
            result.invocation_id,
            self.skill.skill_id,
            self.skill.version,
            result.status,
        )
