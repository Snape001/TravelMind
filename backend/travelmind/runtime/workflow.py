from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from travelmind.runtime import controls, editorial, experts, planning
from travelmind.runtime.execution import RunError, emit, result_identity, run_agent
from travelmind.runtime.journal import ControlContext, control_node, journal_node
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.reflection import ReflectionConfig
from travelmind.runtime.reflection_workflow import ReflectionWorkflow, initial_work
from travelmind.runtime.state import (
    ResearchInput,
    ResearchState,
    evidence_items,
    output_for,
    run_mapping,
)
from travelmind.runtime.travel.integration import incomplete_map, run_travel
from travelmind.runtime.travel.planner import TRAVEL_AGENT_ID, TravelPlanner
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
from travelmind.schemas.domain import Assignment, Evidence, Finding, ReportSection, TaskStatus
from travelmind.schemas.events import (
    AgentMessageEvent,
    ClarificationEvent,
    EvidenceAddedEvent,
    PlanReadyEvent,
    ReportReadyEvent,
    ReportReviewedEvent,
    TaskStatusEvent,
)
from travelmind.schemas.map_data import MapData
from travelmind.schemas.phase4_intake import ChoiceAnswer, ResearchIntakeContext
from travelmind.schemas.research_quality import ExpertResearchResult


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


def expert_assessments(state: ResearchState) -> list[ExpertResearchResult]:
    results = state.get("expert_research", {})
    return [
        ExpertResearchResult.model_validate(results[str(run_id)])
        for run_id in state["usable_research"]
        if str(run_id) in results
    ]


def verified_map_evidence(state: ResearchState) -> frozenset[UUID]:
    return frozenset(
        UUID(entry["evidence_id"])
        for entry in state.get("map_data", {}).get("routes", [])
        if entry["evidence_id"] is not None
    )


def build_research_graph(
    model: StructuredModel,
    *,
    retrieve: experts.EvidenceRetriever | None = None,
    budget: RunBudget | None = None,
    max_revision_rounds: int = 1,
    checkpointer: BaseCheckpointSaver | None = None,
    reflection: ReflectionConfig | None = None,
    travel_intake: ResearchIntakeContext | None = None,
    travel_planner: TravelPlanner | None = None,
):
    """Compile the research workflow. Custom stream items are AgentEvent payloads."""
    if not 0 <= max_revision_rounds <= 2:
        raise ValueError("max_revision_rounds must be between 0 and 2")
    run_budget = budget or RunBudget()
    durable = checkpointer is not None
    reflection_flow = ReflectionWorkflow(reflection, model, retrieve) if reflection else None
    if travel_intake is not None and reflection_flow is None:
        raise ValueError("typed intake graph requires reflection configuration")

    def request_for(state: ResearchState, agent_id: str, *, run_id=None, **context) -> AgentInput:
        return AgentInput(
            task_id=state["task_id"],
            trace_id=state["trace_id"],
            run_id=run_id or uuid4(),
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
        updates = records(result) | {"status": "running", "brief": result.brief}
        if reflection_flow:
            updates["director_run_id"] = result.run_id
        return updates

    async def planner(state: ResearchState) -> dict:
        request = request_for(state, planning.PLANNER)
        social_requested = travel_intake is not None and any(
            isinstance(answer, ChoiceAnswer)
            and answer.question_id == "interests"
            and "social_sentiment" in answer.option_ids
            for answer in travel_intake.research_submission.answers
        )
        result = await run_agent(
            request,
            lambda: planning.plan_research(
                model,
                request,
                focused=reflection_flow is not None,
                travel_requested=travel_intake.travel_route_requested if travel_intake else None,
                social_requested=social_requested,
            ),
        )
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

    def expert_request(state: ResearchState, assignment: Assignment) -> tuple[AgentInput, bool]:
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
            run_id=(
                uuid5(UUID(str(state["task_id"])), f"expert:{assignment.assignment_id}")
                if reflection
                else None
            ),
            assignment=assignment,
            prior_findings=findings,
            evidence=evidence_for(state, findings),
        )
        return request, bool(set(dependencies) - available)

    def dispatch(state: ResearchState) -> list[Send] | Literal["write", "__end__"]:
        if state["status"] == "failed":
            return END
        if state["wave"]:
            ready = set(state["wave"])
            work = [
                expert_request(state, assignment)
                for assignment in state["plan"].assignments
                if assignment.assignment_id in ready
            ]
            if reflection_flow:
                return [
                    Send("travel_expert", request.model_dump(mode="json"))
                    if travel_intake is not None and request.agent_id == TRAVEL_AGENT_ID
                    else initial_work(request, missing_dependencies=missing)
                    for request, missing in work
                ]
            return [
                Send("expert", ExpertRun(request.model_dump(mode="json"), missing))
                for request, missing in work
            ]
        if reflection_flow:
            return "write"
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

    async def travel_expert(payload: dict) -> dict:
        assert travel_intake is not None and travel_intake.travel_route_requested
        request = AgentInput.model_validate(payload)
        snapshot: MapData | None = None

        async def execute() -> ResearchResult:
            nonlocal snapshot
            result, snapshot = await run_travel(request, travel_intake, travel_planner)
            return result

        result = await run_agent(request, execute)
        if isinstance(result, AgentFailure):
            snapshot = incomplete_map(request.task_id, travel_intake, result.error)
        assert request.assignment is not None and snapshot is not None
        return records(result) | {
            "assignment_runs": {request.assignment.assignment_id: result.run_id},
            "map_data": snapshot.model_dump(mode="json"),
            "map_ready": [True],
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
        assessments = expert_assessments(state)
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
            lambda: editorial.write(
                model,
                request,
                inputs,
                state.get("review"),
                experts=assessments,
                verified_map=verified_map_evidence(state),
            ),
        )
        if isinstance(result, AgentFailure):
            return records(result) | publish_status("failed", error=result.error)
        if state.get("map_data", {}).get("status") == "partial":
            author = next(
                (item.agent_id for item in inputs if item.agent_id == TRAVEL_AGENT_ID),
                inputs[0].agent_id,
            )
            notice = ReportSection(
                heading="地图数据完整性",
                body="地图数据不完整。缺失的点位、路段或交通方式请在出行前核验。",
                author_agent_ids=[author],
                evidence_ids=[],
            )
            draft = result.report.model_copy(update={"sections": [*result.report.sections, notice]})
            result = result.model_copy(update={"report": draft})
        return records(result) | {"draft": result.report, "report_id": uuid4()}

    async def review(state: ResearchState) -> dict:
        inputs = contributions(state)
        assessments = expert_assessments(state)
        findings = [finding for item in inputs for finding in item.findings]
        request = request_for(
            state,
            editorial.REVIEWER,
            evidence=editorial_sources(state, findings),
            prior_findings=findings,
            draft=state["draft"],
        )
        result = await run_agent(
            request,
            lambda: editorial.review(
                model,
                request,
                {item.agent_id for item in inputs},
                experts=assessments,
                verified_map=verified_map_evidence(state),
            ),
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

    def running_edges(origin: str, target: str) -> None:
        builder.add_conditional_edges(
            origin, lambda state: state["status"] == "running", {True: target, False: END}
        )

    builder = StateGraph(ResearchState, input_schema=ResearchInput, context_schema=ControlContext)
    nodes = {
        "director": director,
        "planner": planner,
        "schedule": schedule,
        "write": write,
        "review": review,
    }
    if reflection_flow is None:
        nodes.update(expert=expert, cross_examine=cross_examine)
    for name, node in nodes.items():
        builder.add_node(name, journal_node(node) if durable else node)

    if reflection_flow is None:
        builder.add_edge("expert", "schedule")
    running_edges("planner", "schedule")

    if reflection_flow:

        async def reflection_director(state: ResearchState) -> dict:
            return await reflection_flow.director(
                state, lambda current, assignment: expert_request(current, assignment)[0]
            )

        def remaining_checks(state: ResearchState) -> list[Send]:
            return [Send("reflect_expert", item) for item in state["reflection_next"]]

        builder.add_node(
            "reflect_expert",
            journal_node(reflection_flow.expert) if durable else reflection_flow.expert,
        )
        builder.add_node(
            "reflection_director",
            journal_node(reflection_director) if durable else reflection_director,
        )
        builder.add_edge("reflect_expert", "reflection_director")
        if travel_intake is not None:
            builder.add_node(
                "travel_expert", journal_node(travel_expert) if durable else travel_expert
            )
            builder.add_edge("travel_expert", "reflection_director")
        next_step = "before_reflection" if durable else "dispatch_reflection"
        if durable:
            builder.add_node(next_step, control_node(controls.pause_gate))
        else:
            builder.add_node(next_step, lambda _: {})
        builder.add_conditional_edges(
            "reflection_director",
            lambda state: bool(state["reflection_next"]),
            {False: "schedule", True: next_step},
        )
        builder.add_conditional_edges(next_step, remaining_checks, ["reflect_expert"])

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
        if reflection_flow is None:
            builder.add_edge("cross_examine", "before_write")
        running_edges("write", "before_review")
        running_edges("review", "before_write")
        return builder.compile(checkpointer=checkpointer)

    builder.add_edge(START, "director")
    running_edges("director", "planner")
    running_edges("write", "review")
    running_edges("review", "write")
    targets = (
        ["reflect_expert", "write", END]
        if reflection_flow
        else ["expert", "cross_examine", "write", END]
    )
    if travel_intake is not None:
        targets.append("travel_expert")
    builder.add_conditional_edges("schedule", dispatch, targets)
    if reflection_flow is None:
        builder.add_edge("cross_examine", "write")
    graph = builder.compile()
    if reflection_flow:
        graph.config = {"recursion_limit": 100}
    return graph
