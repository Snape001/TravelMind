import asyncio
import unicodedata
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid5

from langgraph.types import Send

from travelmind.research.sources import source_key
from travelmind.runtime import experts, planning, reflection
from travelmind.runtime.execution import RunError, emit, result_identity, run_agent
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.reflection import round_key
from travelmind.runtime.retrieval import research_query
from travelmind.runtime.state import ResearchState, run_mapping
from travelmind.schemas.agents import AgentFailure, AgentInput, ResearchResult
from travelmind.schemas.domain import Assignment
from travelmind.schemas.events import AgentFinishedEvent, AgentMessageEvent, AgentStartedEvent
from travelmind.schemas.research_quality import ExpertResearchResult, ReflectionAction

SEARCH_HINT = {
    "expand_search": "",
    "replace_sources": "官方 原始发布",
    "verify_conflict": "第三方 独立核验 原始发布",
}
MAX_EXCERPT_CHARS = 4_000


class RequestFactory(Protocol):
    def __call__(self, state: ResearchState, assignment: Assignment, /) -> AgentInput: ...


def operation_cost(request: AgentInput) -> int:
    return request.budget.max_output_tokens * request.budget.max_attempts


def bounded_request(request: AgentInput) -> AgentInput:
    budget = request.budget.model_copy(
        update={"max_output_tokens": min(2048, request.budget.max_output_tokens)}
    )
    return request.model_copy(update={"budget": budget})


def initial_work(request: AgentInput, *, missing_dependencies: bool) -> Send:
    assert request.assignment is not None
    objective = request.assignment.objective
    request = bounded_request(request)
    work = reflection.RoundWork(
        request=request,
        as_of=datetime.now(UTC),
        max_age_days=365 if any(term in objective for term in reflection.AGE_SENSITIVE) else None,
        reserved_tokens=operation_cost(request) * reflection.ROUND_MODEL_OPERATIONS,
    )
    return Send(
        "reflect_expert",
        {
            "work": work.model_dump(mode="json"),
            "missing_dependencies": missing_dependencies,
        },
    )


def targeted_request(request: AgentInput, focus: str) -> AgentInput:
    assert request.assignment
    return request.model_copy(
        update={
            "brief": request.brief.model_copy(update={"topics": []}),
            "assignment": request.assignment.model_copy(update={"objective": focus}),
        }
    )


def query_key(request: AgentInput) -> str:
    text = unicodedata.normalize("NFKC", research_query(request)).casefold()
    return "".join(character for character in text if character.isalnum())


def next_search(
    request: AgentInput,
    result: reflection.RoundResult,
    action: ReflectionAction,
    peer_ids: list[str],
) -> reflection.RoundWork:
    focus = " ".join((SEARCH_HINT[action.strategy], *action.target_questions)).strip()
    return reflection.RoundWork(
        request=request,
        as_of=result.as_of,
        max_age_days=result.max_age_days,
        reserved_tokens=result.reserved_tokens
        + operation_cost(request) * (reflection.ROUND_MODEL_OPERATIONS + len(peer_ids)),
        previous=result,
        action=action,
        search_focus=focus,
        query_key=query_key(targeted_request(request, focus)),
        peer_ids=peer_ids,
    )


class ReflectionWorkflow:
    def __init__(
        self,
        config: reflection.ReflectionConfig,
        model: StructuredModel,
        retrieve: experts.EvidenceRetriever | None,
    ):
        self.config = config
        self.model = model
        self.retrieve = retrieve

    async def _score(self, work: reflection.RoundWork, result: reflection.RoundResult) -> None:
        assert self.config.score is not None
        scored = {card.evidence_id for card in result.quality}
        prior_ids = (
            {item.evidence_id for item in work.previous.evidence} if work.previous else set()
        )
        pending = [item for item in result.evidence if item.evidence_id not in scored]
        if self.config.rank and pending:
            pending = list(
                await self.config.rank(
                    work.request.model_copy(update={"evidence": pending}),
                    as_of=work.as_of,
                    max_age_days=work.max_age_days,
                )
            )
        # Favor newly retrieved material over earlier unscored sources on a follow-up round.
        pending.sort(key=lambda item: item.evidence_id in prior_ids)
        size = reflection.QUALITY_BATCH_SIZE
        limit = size * reflection.MAX_QUALITY_BATCHES
        for start in range(0, min(len(pending), limit), size):
            try:
                result.quality += await reflection.score_batch(
                    self.config.score,
                    work.request.model_copy(update={"evidence": pending[start : start + size]}),
                    as_of=work.as_of,
                    max_age_days=work.max_age_days,
                )
            except RunError:
                continue
        if not result.quality:
            raise RunError("QUALITY_UNAVAILABLE", "本分工没有可用的证据评分")

    async def _peer(
        self, work: reflection.RoundWork, agent_id: str, request: AgentInput
    ) -> ResearchResult | AgentFailure:
        assert work.action and work.previous
        related = set(work.action.evidence_ids)
        peer_request = request.model_copy(
            update={
                "run_id": uuid5(request.run_id, f"conflict:{work.previous.round}:{agent_id}"),
                "agent_id": agent_id,
                "assignment": None,
                "prior_findings": [
                    item.finding for item in work.previous.questions if item.state == "conflicting"
                ],
                "evidence": [item for item in request.evidence if item.evidence_id in related],
            }
        )

        async def answer() -> ResearchResult:
            findings = await experts.find(
                self.model,
                peer_request,
                f"{planning.EXPERTS[agent_id]} 答复定向质询。仅核对相反材料的时间、范围和条件，"
                "指出哪项需要第三方证实，保留不确定性。",
                context={"questions": work.action.target_questions},
            )
            return ResearchResult(
                **result_identity(peer_request), kind="research", findings=findings.findings
            )

        return await run_agent(peer_request, answer)

    async def expert(self, payload: dict) -> dict:
        work = reflection.RoundWork.model_validate(payload["work"])
        request, previous = work.request, work.previous
        assert request.assignment
        if payload.get("missing_dependencies"):
            error = RunError("DEPENDENCY_FAILED", "依赖的专家任务没有可用结果")

            async def fail():
                raise error

            result = await run_agent(request, fail)
            return {
                "results": {result.run_id: result},
                "assignment_runs": {request.assignment.assignment_id: request.run_id},
            }
        number = previous.round + 1 if previous else 0
        if number == 0:
            emit(
                AgentStartedEvent(
                    type="agent.started",
                    **result_identity(request),
                    assignment_id=request.assignment.assignment_id,
                )
            )
        known = previous.evidence if previous else request.evidence
        queries = [*previous.queries, work.query_key] if previous else [query_key(request)]
        questions = (
            previous.questions
            if previous
            else [reflection.unresolved(request.assignment.objective[:200], "待补充证据。")]
        )
        result = reflection.RoundResult(
            round=number,
            as_of=work.as_of,
            max_age_days=work.max_age_days,
            evidence=known,
            quality=previous.quality if previous else [],
            questions=questions,
            queries=queries,
            budget=request.budget,
            model_id=previous.model_id if previous else None,
            reserved_tokens=work.reserved_tokens,
        )
        replies = []
        if result.reserved_tokens > self.config.max_output_tokens:
            result.halted = "budget_exhausted"
        else:
            try:
                search_request = (
                    targeted_request(request, work.search_focus) if previous else request
                )
                if self.retrieve:
                    retrieved = await experts.retrieve_evidence(self.retrieve, search_request)
                    evidence = {item.evidence_id: item for item in known}
                    keys = {source_key(item) for item in known}
                    excerpts = {item.excerpt for item in known}
                    for item in retrieved:
                        if evidence.setdefault(item.evidence_id, item) != item:
                            raise RunError("EVIDENCE_CONFLICT", "同一证据 ID 对应不同资料")
                        if source_key(item) not in keys and item.excerpt not in excerpts:
                            result.new_sources = True
                    result.evidence = list(evidence.values())

                if result.evidence:
                    await self._score(work, result)
                request = request.model_copy(
                    update={
                        "evidence": [
                            item.model_copy(update={"excerpt": item.excerpt[:MAX_EXCERPT_CHARS]})
                            for item in result.evidence
                        ],
                    }
                )
                if work.peer_ids:
                    replies = await asyncio.gather(
                        *(self._peer(work, agent_id, request) for agent_id in work.peer_ids)
                    )
                if request.evidence:
                    retained = [item.finding for item in questions if item.state == "sufficient"]
                    candidates = await experts.find(
                        self.model,
                        request,
                        f"{planning.EXPERTS[request.agent_id]} 针对分工和目标问题提出候选结论。"
                        "保留有证据支持的已核实结论；标出有争议或缺少证据的问题。",
                        context={
                            "target_questions": work.action.target_questions if work.action else [],
                            "verified_findings": [
                                item.model_dump(mode="json") for item in retained
                            ],
                        },
                    )
                    result.questions = await reflection.assess(
                        self.model,
                        request,
                        candidates.findings,
                        result.quality,
                        [item.question for item in questions] if previous else [],
                        [
                            finding
                            for reply in replies
                            if isinstance(reply, ResearchResult)
                            for finding in reply.findings
                        ],
                    )
                    result.model_id = self.config.model_id
            except RunError:
                result.halted = "assessment_unavailable"
        emit(
            AgentMessageEvent(
                type="agent.message",
                **result_identity(request),
                message=f"已完成第 {number} 轮补充核验。" if number else "已完成首轮局部核验。",
            )
        )
        return {
            "reflection_rounds": {
                round_key(request.run_id, number): result.model_dump(mode="json"),
            },
            "results": {reply.run_id: reply for reply in replies},
        }

    async def director(self, state: ResearchState, request_for: RequestFactory) -> dict:
        """Authorize only bounded, targeted work after all experts in this wave report."""
        assigned = run_mapping(state["assignment_runs"])
        rounds = state.get("reflection_rounds", {})
        max_rounds = reflection.MAX_ROUNDS[state["brief"].depth]
        decisions, completed, exports, additional = {}, {}, {}, []
        exchange = state.get("reflection_exchange_used", False)
        wave = set(state["wave"])
        for assignment in state["plan"].assignments:
            if assignment.assignment_id not in wave or assignment.assignment_id in assigned:
                continue
            request = request_for(state, assignment)
            run_id = request.run_id
            latest = max(
                number for number in range(max_rounds + 1) if round_key(run_id, number) in rounds
            )
            key = round_key(run_id, latest)
            result = reflection.RoundResult.model_validate(rounds[key])
            request = request.model_copy(update={"budget": result.budget})
            batch_cost = reflection.ROUND_MODEL_OPERATIONS * operation_cost(request)
            exhausted = (
                not self.retrieve
                or max_rounds > self.config.max_follow_up_searches
                and latest >= self.config.max_follow_up_searches
                or result.reserved_tokens + batch_cost > self.config.max_output_tokens
            )
            action = reflection.decide(result, max_rounds=max_rounds, budget_exhausted=exhausted)
            if action.stop_reason is None:
                peers = []
                if action.state == "conflicting" and not exchange:
                    spare = self.config.max_output_tokens - result.reserved_tokens - batch_cost
                    peer_limit = min(2, spare // operation_cost(request))
                    peers = [
                        item.agent_id
                        for item in state["plan"].assignments
                        if item.agent_id != request.agent_id
                    ][:peer_limit]
                work = next_search(request, result, action, peers)
                if work.query_key in result.queries or not work.query_key:
                    action = reflection.decide(
                        result, max_rounds=max_rounds, budget_exhausted=False, duplicate_query=True
                    )
                else:
                    additional.append({"work": work.model_dump(mode="json")})
                    if peers:
                        exchange = True
            decisions[key] = {"run_id": str(run_id), "action": action.model_dump(mode="json")}
            if action.stop_reason is not None:
                final = self._finish(request, result, action)
                completed[assignment.assignment_id] = run_id
                exports[run_id] = final
        if additional and (director_run_id := state.get("director_run_id")):
            message = f"已安排 {len(additional)} 项定向补查。"
            if exchange and not state.get("reflection_exchange_used"):
                message += "已邀请其他专家核对冲突来源。"
            emit(
                AgentMessageEvent(
                    type="agent.message",
                    agent_id=planning.DIRECTOR,
                    run_id=director_run_id,
                    message=message,
                )
            )
        return {
            "reflection_decisions": decisions,
            "reflection_next": additional,
            "reflection_exchange_used": exchange,
            "assignment_runs": completed,
            "expert_research": {
                str(run_id): item.model_dump(mode="json") for run_id, item in exports.items()
            },
            "results": {
                run_id: item.research.model_copy(
                    update={
                        "evidence": [
                            evidence
                            for evidence in item.research.evidence
                            if evidence.evidence_id not in state["evidence"]
                            and str(evidence.evidence_id) not in state["evidence"]
                        ],
                    }
                )
                for run_id, item in exports.items()
            },
        }

    def _finish(
        self, request: AgentInput, result: reflection.RoundResult, action: ReflectionAction
    ) -> ExpertResearchResult:
        assert request.assignment
        research = ResearchResult(
            **result_identity(request),
            kind="research",
            evidence=result.evidence,
            findings=[item.finding for item in result.questions],
        )
        final = ExpertResearchResult(
            assignment_id=request.assignment.assignment_id,
            research=research,
            quality=result.quality,
            reflection=action,
            unresolved_questions=[
                item.question for item in result.questions if item.state != "sufficient"
            ],
        )

        emit(
            AgentFinishedEvent(
                type="agent.finished", **result_identity(request), status="completed"
            )
        )
        return final
