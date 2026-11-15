from typing import Literal
from uuid import UUID

from pydantic import Field

from travelmind.evaluation.contracts import CandidateRun, Usage
from travelmind.schemas.agents import ResearchResult
from travelmind.schemas.api import EvidenceRead, ReportRead, TaskRead
from travelmind.schemas.domain import Contract, Evidence
from travelmind.schemas.events import AgentEvent
from travelmind.schemas.research_quality import ExpertResearchResult


class TaskArchive(Contract):
    """Files already exported from the public task API and optional sidecar observations."""

    task: TaskRead
    evidence: list[EvidenceRead] = Field(default_factory=list)
    reports: list[ReportRead] = Field(default_factory=list)
    events: list[AgentEvent] | None = None
    ranked_evidence_ids: list[UUID] | None = None
    research: list[ResearchResult] | None = None
    expert_research: list[ExpertResearchResult] | None = None
    usage: Usage = Field(default_factory=Usage)

    def candidate(
        self, case_key: str, source: str, *, origin: Literal["recorded", "demo"] = "recorded"
    ) -> CandidateRun:
        task_id = self.task.task_id
        if any(item.task_id != task_id for item in [*self.evidence, *self.reports]):
            raise ValueError("archive reports and evidence must belong to the requested task")
        if any(
            event.task_id != task_id or event.trace_id != self.task.trace_id
            for event in self.events or ()
        ):
            raise ValueError("archive events must belong to the requested task and trace")
        if len({report.version for report in self.reports}) != len(self.reports):
            raise ValueError("archive report versions must be unique")
        report = max(self.reports, key=lambda item: item.version, default=None)
        failure_code = None
        for event in reversed(self.events or []):
            payload = event.payload
            if event.task_id == task_id and payload.type == "task.status":
                if payload.status == "failed" and payload.error is not None:
                    failure_code = payload.error.code
                break
        return CandidateRun(
            case_key=case_key,
            task_id=task_id,
            origin=origin,
            source=source,
            status=self.task.status,
            failure_code=failure_code,
            evidence=[
                Evidence.model_validate(item.model_dump(exclude={"task_id"}))
                for item in self.evidence
            ],
            report=report.content if report else None,
            report_id=report.report_id if report else None,
            events=self.events,
            ranked_evidence_ids=self.ranked_evidence_ids,
            research=self.research,
            expert_research=self.expert_research,
            usage=self.usage,
        )
