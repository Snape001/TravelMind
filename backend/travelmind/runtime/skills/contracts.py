from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from travelmind.schemas.domain import AgentId, Contract, ShortText, Text
from travelmind.schemas.phase4_experts import ExpertSkill

FindingId = Annotated[int, Field(ge=1)]
Note = Annotated[Text, Field(max_length=500)]


class SkillDefinition(ExpertSkill):
    max_context_chars: int = Field(ge=1000, le=150_000)


class DirectorInput(Contract):
    clarification_answers: list[dict[str, str]] = Field(default_factory=list)


class ResearchInput(Contract):
    # The assignment, available evidence and prior findings are in AgentInput.
    round_focus: list[ShortText] = Field(default_factory=list, max_length=10)


class CatalogFinding(Contract):
    id: FindingId
    agent_ids: list[AgentId] = Field(min_length=1)
    statement: Text
    evidence_ids: list[UUID]
    support: Literal["supported", "insufficient", "disputed"]


class OutlineSection(Contract):
    heading: ShortText
    purpose: Note
    finding_ids: list[FindingId] = Field(min_length=1)


class ReportOutline(Contract):
    title: ShortText
    sections: list[OutlineSection] = Field(min_length=1, max_length=8)
    omitted_finding_ids: list[FindingId] = Field(default_factory=list)


class OutlineInput(Contract):
    findings: list[CatalogFinding]
    unresolved_questions: list[ShortText]
    revision_notes: list[Note]


class SynthesisInput(OutlineInput):
    outline: ReportOutline


class SynthesisPoint(Contract):
    finding_ids: list[FindingId] = Field(min_length=1)
    summary: Note


class SectionSynthesis(Contract):
    heading: ShortText
    points: list[SynthesisPoint] = Field(min_length=1, max_length=12)
    gaps: list[Note] = Field(default_factory=list, max_length=8)
    conflicts: list[Note] = Field(default_factory=list, max_length=8)


class EvidenceSynthesis(Contract):
    sections: list[SectionSynthesis] = Field(min_length=1, max_length=8)


class WritingInput(SynthesisInput):
    synthesis: EvidenceSynthesis


class ReviewInput(Contract):
    contributor_ids: list[AgentId]
    expert_assessments: list[dict] = Field(default_factory=list)
