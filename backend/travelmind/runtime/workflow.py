from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from travelmind.runtime import controls, editorial, experts, planning
from travelmind.runtime.execution import RunError, emit, result_identity, run_agent
from travelmind.runtime.journal import ControlContext, control_node, journal_node
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.state import (
    ResearchInput,
    ResearchState,
    evidence_items,
    output_for,
    run_mapping,
)
from travelmind.schemas.agents import (
    AgentFailure,
    AgentInput,
    AgentResult,
    BriefResult,
    ClarificationResult,
    ErrorInfo,
    ResearchResult,
    RunBudget,
)
from travelmind.schemas.domain import Assignment, Evidence, Finding, TaskStatus
from travelmind.schemas.events import (
    AgentMessageEvent,
    ClarificationEvent,
    EvidenceAddedEvent,
    PlanReadyEvent,
    ReportReadyEvent,
    ReportReviewedEvent,
    TaskStatusEvent,
)


@dataclass(frozen=True)
class ExpertRun:
    request: dict
    missing_dependencies: bool


@dataclass(frozen=True)
class ExpertExchange:
    request: dict
    target_agent_id: str
    target_run_id: str


def records(*results: AgentResult) -> dict:
    return {"results": {result.run_id: result for result in results}}


def publish_status(status: TaskStatus, *, error: ErrorInfo | None = None) -> dict:
    emit(TaskStatusEvent(type="task.status", status=status, error=error))
    return {"status": status, "failure": error} if error else {"status": status}


def evidence_for(state: ResearchState, findings: list[Finding]) -> list[Evidence]:
    ids = dict.fromkeys(evidence_id for finding in findings for evidence_id in finding.evidence_ids)
    known = evidence_items(state)
    return [known[evidence_id] for evidence_id in ids]


def editorial_sources(state: ResearchState, findings: list[Finding]) -> list[Evidence]:
    ids = dict.fromkeys(evidence_id for finding in findings for evidence_id in finding.evidence_ids)
    draft = state.get("draft")
    if draft is not None:
        for section in draft.sections:
            ids.update(dict.fromkeys(section.evidence_ids))
    known = evidence_items(state)
    return [known[evidence_id] for evidence_id in ids if evidence_id in known]


def contributions(state: ResearchState) -> list[ResearchResult]:
    run_ids = run_mapping(state["assignment_runs"])
    available = set(state["usable_research"])
    challenge_runs = run_mapping(state["challenge_runs"])
    results = []
    for assignment in state["plan"].assignments:
        run_id = run_ids[assignment.assignment_id]
        if run_id in available:
            results.append(output_for(state, run_id))
            if run_id in challenge_runs:
                results.append(output_for(state, challenge_runs[run_id]))
    return results


def build_research_graph(
    model: StructuredModel,
    *,
    retrieve: experts.EvidenceRetriever | None = None,
    budget: RunBudget | None = None,
    max_revision_rounds: int = 1,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compile the async A-line workflow. Custom stream items are AgentEvent payloads."""
    if not 0 <= max_revision_rounds <= 2:
        raise ValueError("max_revision_rounds must be between 0 and 2")
    run_budget = budget or RunBudget()
    durable = checkpointer is not None

    def request_for(state: ResearchState, agent_id: str, **context) -> AgentInput:
        return AgentInput(
            task_id=state["task_id"],
            trace_id=state["trace_id"],
            run_id=uuid4(),
            agent_id=agent_id,
            brief=state["brief"],
            budget=run_budget,
            **context,
        )

    async def director(state: ResearchState) -> dict:
        publish_status("running")
        request = request_for(state, planning.DIRECTOR)
        result = await run_agent(
            request, lambda: planning.understand(model, request, state.get("clarification_history"))
        )
        if isinstance(result, AgentFailure):
            return records(result) | publish_status("failed", error=result.error)
        if isinstance(result, ClarificationResult):
            emit(ClarificationEvent(type="task.clarification", questions=result.questions))
            updates = (
                records(result)
                | publish_status("needs_clarification")
                | {"questions": result.questions}
            )
            if durable:
                updates["pending_clarification"] = controls.clarification_for(
                    state, result.questions
                )
            return updates
        assert isinstance(result, BriefResult)
        return records(result) | {"status": "running", "brief": result.brief}

    async def planner(state: ResearchState) -> dict:
        request = request_for(state, planning.PLANNER)
        result = await run_agent(request, lambda: planning.plan_research(model, request))
        if isinstance(result, AgentFailure):
            return records(result) | publish_status("failed", error=result.error)
        emit(PlanReadyEvent(type="plan.ready", plan=result.plan))
        return records(result) | {"plan": result.plan}

    async def schedule(state: ResearchState) -> dict:
        assigned = run_mapping(state["assignment_runs"])
        usable = state.get("usable_research", []).copy()
        known = evidence_items(state)
        for assignment_id in state.get("wave", []):
            result = output_for(state, assigned[assignment_id])
            if not isinstance(result, ResearchResult):
                continue
            evidence = {item.evidence_id: item for item in result.evidence}
            if any(key in known and known[key] != item for key, item in evidence.items()):
                emit(
                    AgentMessageEvent(
                        type="agent.message",
                        agent_id=result.agent_id,
                        run_id=result.run_id,
                        message="证据 ID 冲突，已排除本次研究结果。",
                    )
                )
                continue
            new = {key: item for key, item in evidence.items() if key not in known}
            if new:
                known.update(new)
                emit(
                    EvidenceAddedEvent(
                        type="evidence.added",
                        agent_id=result.agent_id,
                        run_id=result.run_id,
                        evidence_ids=list(new),
                    )
                )
            usable.append(result.run_id)
        wave = [
            task.assignment_id
            for task in state["plan"].assignments
            if task.assignment_id not in assigned and set(task.depends_on) <= assigned.keys()
        ]
        updates = {"wave": wave, "usable_research": usable, "evidence": known}
        if not wave and not usable:
            error = ErrorInfo(
                code="NO_RESEARCH_RESULTS", message="没有可用的专家研究结果", retryable=True
            )
            return updates | publish_status("failed", error=error)
        return updates

    def expert_request(state: ResearchState, assignment: Assignment) -> ExpertRun:
        assigned = run_mapping(state["assignment_runs"])
        dependencies = [assigned[parent] for parent in assignment.depends_on]
        available = set(state["usable_research"])
        findings = [
            finding
            for run_id in dependencies
            if run_id in available
            for finding in output_for(state, run_id).findings
        ]
        request = request_for(
            state,
            assignment.agent_id,
            assignment=assignment,
            prior_findings=findings,
            evidence=evidence_for(state, findings),
        )
        return ExpertRun(
            request.model_dump(mode="json"),
            bool(set(dependencies) - available),
        )

    def dispatch(state: ResearchState) -> list[Send] | Literal["write", "__end__"]:
        if state["status"] == "failed":
            return END
        if state["wave"]:
            ready = set(state["wave"])
            return [
                Send("expert", expert_request(state, assignment))
                for assignment in state["plan"].assignments
                if assignment.assignment_id in ready
            ]
        successful = contributions(state)
        if len(successful) < 2:
            return "write"
        return [
            Send(
                "cross_examine",
                ExpertExchange(
                    request_for(
                        state,
                        reviewer.agent_id,
                        prior_findings=target.findings,
                        evidence=evidence_for(state, target.findings),
                    ).model_dump(mode="json"),
                    target.agent_id,
                    str(target.run_id),
                ),
            )
            for reviewer, target in zip(successful, successful[1:] + successful[:1], strict=True)
        ]

    async def expert(work: ExpertRun) -> dict:
        request = AgentInput.model_validate(work.request)

        async def execute() -> ResearchResult:
            if work.missing_dependencies:
                raise RunError("DEPENDENCY_FAILED", "依赖的专家任务没有可用结果")
            return await experts.research(model, retrieve, request)

        result = await run_agent(request, execute)
        assert request.assignment is not None
        return records(result) | {
            "assignment_runs": {request.assignment.assignment_id: result.run_id},
        }

    async def cross_examine(work: ExpertExchange) -> dict:
        asker = AgentInput.model_validate(work.request)
        question = await run_agent(
            asker, lambda: experts.challenge(model, asker, work.target_agent_id)
        )
        if isinstance(question, AgentFailure):
            return records(question)
        emit(
            AgentMessageEvent(
                type="agent.message",
                **result_identity(asker),
                message=f"向 {work.target_agent_id} 质询：{question.question}",
            )
        )
        finding = asker.prior_findings[question.finding_index]
        answer_request = asker.model_copy(
            update={
                "run_id": uuid4(),
                "agent_id": work.target_agent_id,
                "prior_findings": [finding],
                "evidence": [
                    item for item in asker.evidence if item.evidence_id in finding.evidence_ids
                ],
            }
        )
        result = await run_agent(
            answer_request, lambda: experts.answer(model, answer_request, question)
        )
        if isinstance(result, AgentFailure):
            return records(result)
        return records(result) | {"challenge_runs": {UUID(work.target_run_id): result.run_id}}

    async def write(state: ResearchState) -> dict:
        inputs = contributions(state)
        request = request_for(
            state,
            editorial.WRITER,
            evidence=editorial_sources(
                state, [finding for item in inputs for finding in item.findings]
            ),
            draft=state.get("draft"),
        )
        result = await run_agent(
            request,
            lambda: editorial.write(model, request, inputs, state.get("review")),
        )
        if isinstance(result, AgentFailure):
            return records(result) | publish_status("failed", error=result.error)
        return records(result) | {"draft": result.report, "report_id": uuid4()}

    async def review(state: ResearchState) -> dict:
        inputs = contributions(state)
        findings = [finding for item in inputs for finding in item.findings]
        request = request_for(
            state,
            editorial.REVIEWER,
            evidence=editorial_sources(state, findings),
            prior_findings=findings,
            draft=state["draft"],
        )
        result = await run_agent(
            request, lambda: editorial.review(model, request, {item.agent_id for item in inputs})
        )
        if isinstance(result, AgentFailure):
            return records(result) | publish_status("failed", error=result.error)
        decision = result.review.decision
        round_number = state.get("review_round", 0) + 1
        report_id = state["report_id"]
        emit(
            ReportReviewedEvent(
                type="report.reviewed",
                **result_identity(request),
                report_id=report_id,
                decision=decision,
            )
        )
        updates = records(result) | {"review": result.review, "review_round": round_number}
        if decision == "approved":
            emit(
                ReportReadyEvent(
                    type="report.ready",
                    **result_identity(request),
                    report_id=report_id,
                    version=round_number,
                )
            )
            return updates | publish_status("completed")
        if round_number > max_revision_rounds:
            error = ErrorInfo(
                code="REVIEW_REJECTED", message="报告达到返工上限，仍未通过审核", retryable=False
            )
            return updates | publish_status("failed", error=error)
        return updates

    builder = StateGraph(ResearchState, input_schema=ResearchInput, context_schema=ControlContext)
    for name, node in {
        "director": director,
        "planner": planner,
        "schedule": schedule,
        "expert": expert,
        "cross_examine": cross_examine,
        "write": write,
        "review": review,
    }.items():
        builder.add_node(name, journal_node(node) if durable else node)

    if durable:

        async def initialize(state: ResearchState) -> dict:
            return {"consumed": [state["start_instruction_id"]]}

        builder.add_node("initialize", journal_node(initialize))
        builder.add_node("clarification", control_node(controls.clarification))
        for stage in ("director", "planner", "dispatch", "write", "review"):
            gate = f"before_{stage}"
            builder.add_node(gate, control_node(controls.pause_gate))
            if stage != "dispatch":
                builder.add_edge(gate, stage)
        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "before_director")
        builder.add_conditional_edges(
            "director",
            lambda state: state["status"],
            {
                "running": "before_planner",
                "needs_clarification": "clarification",
                "failed": END,
            },
        )
        builder.add_edge("clarification", "before_director")
        builder.add_edge("expert", "schedule")
        builder.add_conditional_edges(
            "schedule",
            lambda state: state["status"],
            {
                "running": "before_dispatch",
                "failed": END,
            },
        )
        builder.add_conditional_edges(
            "before_dispatch",
            lambda state: "before_write" if (target := dispatch(state)) == "write" else target,
            {
                "before_write": "before_write",
                END: END,
            },
        )
        builder.add_edge("cross_examine", "before_write")
        for origin, target in {
            "planner": "schedule",
            "write": "before_review",
            "review": "before_write",
        }.items():
            builder.add_conditional_edges(
                origin,
                lambda state: state["status"] == "running",
                {True: target, False: END},
            )
        return builder.compile(checkpointer=checkpointer)

    builder.add_edge(START, "director")
    for node, next_node in [
        ("director", "planner"),
        ("planner", "schedule"),
        ("write", "review"),
        ("review", "write"),
    ]:
        builder.add_conditional_edges(
            node, lambda state: state["status"] == "running", {True: next_node, False: END}
        )
    builder.add_conditional_edges("schedule", dispatch, ["expert", "cross_examine", "write", END])
    builder.add_edge("expert", "schedule")
    builder.add_edge("cross_examine", "write")
    return builder.compile()
