from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl

from travelmind.schemas.domain import Contract, ShortText, SourceType, Text
from travelmind.schemas.events import EventSequence, RunEvent
from travelmind.schemas.phase4_intake import ContractKey, NonnegativeInt, PositiveInt
from travelmind.schemas.research_quality import QualityLevel


class AgentUsage(Contract):
    """Provider metrics and a separately authored, safe prompt preview for one call."""

    call_id: UUID
    message_event_id: UUID | None = None
    provider: ContractKey
    model: ShortText
    prompt_preview: Annotated[Text, Field(max_length=280)] | None = None
    input_tokens: NonnegativeInt | None
    output_tokens: NonnegativeInt | None


class AgentUsageEvent(RunEvent):
    type: Literal["agent.usage"]
    usage: AgentUsage


class SourceEvent(RunEvent):
    source_id: UUID


class SourceDiscoveredEvent(SourceEvent):
    type: Literal["research.source.discovered"]
    source_type: SourceType
    title: ShortText
    publisher: ShortText | None = None
    source_url: HttpUrl | None = None


class SourceFetchEvent(SourceEvent):
    type: Literal["research.source.fetch"]
    attempt: PositiveInt
    status: Literal["started", "succeeded", "failed", "skipped"]
    error_code: ShortText | None = None


class SourceScoredEvent(SourceEvent):
    type: Literal["research.source.scored"]
    evidence_id: UUID
    assignment_id: UUID
    policy_version: Literal["v1"] = "v1"
    level: QualityLevel
    total_score: Annotated[NonnegativeInt, Field(le=10)]


class SourceCitedEvent(SourceEvent):
    type: Literal["research.source.cited"]
    report_id: UUID
    report_version: PositiveInt
    evidence_id: UUID


ResearchBrowserPayload = Annotated[
    SourceDiscoveredEvent
    | SourceFetchEvent
    | SourceScoredEvent
    | SourceCitedEvent
    | AgentUsageEvent,
    Field(discriminator="type"),
]


class ResearchBrowserEvent(Contract):
    version: Literal[1] = 1
    event_id: UUID
    task_id: UUID
    trace_id: UUID
    sequence: EventSequence
    occurred_at: AwareDatetime
    payload: ResearchBrowserPayload
