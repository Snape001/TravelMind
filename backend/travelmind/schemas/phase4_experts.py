from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from travelmind.schemas.agents import ErrorInfo, RunBudget
from travelmind.schemas.domain import AgentId, Contract, ShortText, Text
from travelmind.schemas.phase4_intake import ContractKey, NonnegativeInt, PositiveInt

ExpertTier = Literal["leadership", "council", "editorial"]


class SkillRef(Contract):
    skill_id: ContractKey
    version: PositiveInt


class ExpertProfile(Contract):
    agent_id: AgentId
    name: ShortText | None = None
    title: ShortText
    tier: ExpertTier
    description: Text
    portrait: Annotated[str, Field(pattern=r"^/images/experts/[a-z0-9_-]+\.webp$")] | None = None
    fields: list[ShortText] = Field(default_factory=list)
    tools: list[ShortText] = Field(default_factory=list)
    skills: list[SkillRef] = Field(default_factory=list)


class ExpertProfilesRead(Contract):
    experts: list[ExpertProfile]


class SkillBudget(Contract):
    model: RunBudget
    tool_calls: NonnegativeInt


class ExpertSkill(SkillRef):
    title: ShortText
    agent_ids: list[AgentId] = Field(min_length=1)
    stage: Literal["research", "plan_route", "outline", "synthesize", "write", "edit", "review"]
    use_when: list[ShortText] = Field(min_length=1)
    input_schema: ContractKey
    output_schema: ContractKey
    allowed_tools: list[ContractKey] = Field(default_factory=list)
    steps: list[Text] = Field(min_length=1)
    quality_requirements: list[Text] = Field(min_length=1)
    budget: SkillBudget


class ExpertSkillInvocation(Contract):
    invocation_id: UUID
    skill: SkillRef
    task_id: UUID
    trace_id: UUID
    run_id: UUID
    agent_id: AgentId
    input: dict[str, JsonValue]


class ExpertSkillResult(Contract):
    invocation_id: UUID
    status: Literal["completed", "failed"]
    output: dict[str, JsonValue] | None = None
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def require_result(self) -> Self:
        if self.status == "completed":
            if self.output is None or self.error is not None:
                raise ValueError("completed skill requires output only")
        elif self.error is None or self.output is not None:
            raise ValueError("failed skill requires error only")
        return self
