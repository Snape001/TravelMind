from time import perf_counter
from uuid import UUID, uuid4, uuid5

from travelmind.runtime.execution import emit, result_identity
from travelmind.runtime.travel.planner import (
    TRAVEL_PROFILE,
    TravelPlan,
    TravelPlanner,
    TravelSkillInput,
)
from travelmind.runtime.travel.rules import route_evidence
from travelmind.schemas.agents import AgentInput, ErrorInfo, ResearchResult
from travelmind.schemas.domain import Evidence, Finding
from travelmind.schemas.events import ToolFinishedEvent, ToolStartedEvent
from travelmind.schemas.map_data import (
    MapData,
    MapDay,
    MapIssue,
    MapLeg,
    MapPlace,
    MapRoute,
    MapStop,
)
from travelmind.schemas.phase4_experts import ExpertSkillInvocation
from travelmind.schemas.phase4_intake import ResearchIntakeContext


def incomplete_map(task_id: UUID, intake: ResearchIntakeContext, issue: ErrorInfo) -> MapData:
    return MapData(
        task_id=task_id,
        status="partial",
        destination=intake.destination,
        places=[MapPlace(poi=intake.destination)],
        issues=[MapIssue(code=issue.code, message=issue.message)],
    )


def map_snapshot(task_id: UUID, plan: TravelPlan) -> tuple[MapData, list[Evidence]]:
    places = {plan.anchor.poi_id: MapPlace(poi=plan.anchor)}
    for area in plan.areas:
        for place in area.places:
            places.setdefault(place.poi_id, MapPlace(poi=place))
    days, routes, sources = [], {}, {}
    issues = [MapIssue.model_validate(gap.model_dump(exclude={"retryable"})) for gap in plan.gaps]
    partial = plan.feasibility == "partial"
    requested_modes = set(plan.preferences.transport_modes or ("walking", "transit"))
    for day_number, day in enumerate(plan.days, 1):
        stops, legs = [], []
        for stop in day.stops:
            poi_id = stop.place.poi_id
            if detail := stop.amap_details:
                places[poi_id] = MapPlace(
                    poi=stop.place,
                    detail_fetched_at=detail.fetched_at,
                    category=detail.category[:200] if detail.category else None,
                )
            stops.append(
                MapStop(
                    poi_id=poi_id,
                    suggested_start=stop.suggested_start,
                    suggested_minutes=stop.suggested_minutes,
                )
            )
        for comparison in day.connections:
            legs.append(
                MapLeg(
                    selected_route_id=comparison.selected_route_id,
                    alternative_route_ids=[route.route_id for route in comparison.alternatives],
                )
            )
            if requested_modes - {route.mode for route in comparison.alternatives}:
                partial = True
            for route in comparison.alternatives:
                evidence_id = None
                if route.route_id == comparison.selected_route_id:
                    source = route_evidence(route, day.on_date)
                    evidence_id = uuid5(task_id, str(source.evidence_id))
                    sources[evidence_id] = source.model_copy(update={"evidence_id": evidence_id})
                    if route.polyline is None:
                        partial = True
                        issues.append(
                            MapIssue(
                                code="MAP_GEOMETRY_INCOMPLETE",
                                message="路线折线缺失或分段接点不连续",
                                origin_poi_id=route.origin.poi_id,
                                poi_id=route.destination.poi_id,
                                mode=route.mode,
                            )
                        )
                routes[route.route_id] = MapRoute(
                    route=route,
                    evidence_id=evidence_id,
                    segments=comparison.segments_by_route.get(route.route_id, []),
                )
        days.append(MapDay(day_number=day_number, on_date=day.on_date, stops=stops, legs=legs))
    return MapData(
        task_id=task_id,
        status="partial" if partial or not routes else "complete",
        destination=plan.anchor,
        places=list(places.values()),
        days=days,
        routes=list(routes.values()),
        issues=issues,
    ), list(sources.values())


async def run_travel(
    request: AgentInput,
    intake: ResearchIntakeContext,
    planner: TravelPlanner | None,
) -> tuple[ResearchResult, MapData]:
    tool = {**result_identity(request), "tool_call_id": uuid4()}
    emit(ToolStartedEvent(type="tool.started", **tool, tool_name="travel_itinerary"))
    started = perf_counter()
    error = None
    sources = []
    if planner is None:
        error = ErrorInfo(
            code="MAP_PLANNER_UNAVAILABLE",
            message="地图规划服务暂不可用",
            retryable=True,
        )
    else:
        result = await planner.invoke(
            ExpertSkillInvocation(
                invocation_id=uuid5(request.run_id, "travel_itinerary:v1"),
                skill=TRAVEL_PROFILE.skills[0],
                task_id=request.task_id,
                trace_id=request.trace_id,
                run_id=request.run_id,
                agent_id=request.agent_id,
                input=TravelSkillInput(intake=intake, evidence=request.evidence).model_dump(
                    mode="json"
                ),
            )
        )
        if result.error:
            error = result.error
        else:
            snapshot, sources = map_snapshot(
                request.task_id, TravelPlan.model_validate(result.output)
            )
    if error is not None:
        snapshot = incomplete_map(request.task_id, intake, error)
    emit(
        ToolFinishedEvent(
            type="tool.finished",
            **tool,
            status="failed" if error else "completed",
            error=error,
            duration_ms=int((perf_counter() - started) * 1000),
        )
    )
    findings = [
        Finding(statement=item.excerpt, evidence_ids=[item.evidence_id], support="supported")
        for item in sources
    ]
    if snapshot.status == "partial":
        findings.append(
            Finding(
                statement="地图数据不完整，部分路段或交通方式仍需核验。",
                evidence_ids=[],
                support="insufficient",
            )
        )
    return ResearchResult(
        **result_identity(request),
        kind="research",
        findings=findings,
        evidence=sources,
    ), snapshot
