import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from travelmind.research.amap_routes import AmapRoutes
from travelmind.research.engine import ResearchEngine
from travelmind.runtime.execution import RunError
from travelmind.runtime.travel.places import AmapPlaces, AmapPoiDetail
from travelmind.runtime.travel.rules import (
    DAILY_BUDGET,
    VisitArea,
    VisitSource,
    access_sources,
    group_areas,
    next_visit_start,
    route_evidence,
    shortlist,
    stay_minutes,
    trip_window,
)
from travelmind.schemas.agents import ErrorInfo, RunBudget
from travelmind.schemas.domain import Contract, Evidence, ResearchPlan
from travelmind.schemas.map_data import MapSegment
from travelmind.schemas.phase4_experts import (
    ExpertProfile,
    ExpertSkill,
    ExpertSkillInvocation,
    ExpertSkillResult,
    SkillBudget,
    SkillRef,
)
from travelmind.schemas.phase4_intake import (
    Destination,
    ResearchIntakeContext,
    TravelMode,
    TravelPreferences,
)
from travelmind.schemas.phase4_sources import TravelRoute, TravelRoutesRead

TRAVEL_AGENT_ID = "travel_planning"
MAX_VISITS = 7
START_MINUTE = 9 * 60
PERSONAL_MODES = {"driving", "cycling"}
TRAVEL_SKILL = ExpertSkill(
    skill_id="travel_itinerary",
    version=1,
    title="可执行行程规划",
    agent_ids=[TRAVEL_AGENT_ID],
    stage="plan_route",
    use_when=["用户在已确认目的地的研究问卷中选择了旅游路线"],
    input_schema="travel_skill_input",
    output_schema="travel_plan",
    allowed_tools=["amap_poi_search", "amap_poi_detail", "amap_route", "official_notice_search"],
    steps=[
        "按行政区分析景点分布",
        "组合景点并按真实通勤耗时排序",
        "建议停留时间",
        "比较用户允许的交通方式",
        "评估行程强度及时间预算",
        "核对预约、开放、限制和门票来源",
        "标记不可用路段和计划可行性",
    ],
    quality_requirements=[
        "只有高德实际返回的路段才能写入地图和证据",
        "建议停留时间与高德通勤时间分开记录",
        "景区政策保留原文、来源及适用范围；缺失时标记待核验",
    ],
    budget=SkillBudget(model=RunBudget(max_attempts=1), tool_calls=72),
)
TRAVEL_PROFILE = ExpertProfile(
    agent_id=TRAVEL_AGENT_ID,
    name="陆知行",
    title="旅游规划专家",
    tier="council",
    portrait="/images/experts/tourism_resources.webp",
    description="根据已确认的目的地、出行偏好和地图路段安排游览，核对预约及出行限制。",
    fields=["区域与景点组合", "多方式交通", "行程预算与可行性"],
    tools=["高德地图", "官方公告检索"],
    skills=[SkillRef(skill_id=TRAVEL_SKILL.skill_id, version=TRAVEL_SKILL.version)],
)


def validate_travel_assignment(intake: ResearchIntakeContext, plan: ResearchPlan) -> None:
    count = sum(assignment.agent_id == TRAVEL_AGENT_ID for assignment in plan.assignments)
    if count != int(intake.travel_route_requested):
        raise ValueError("路线专家分工须与用户所选研究方向一致")


class TravelSkillInput(Contract):
    intake: ResearchIntakeContext
    evidence: list[Evidence] = Field(default_factory=list)


class PlanningGap(Contract):
    code: str
    message: str
    retryable: bool = False
    origin_poi_id: str | None = None
    poi_id: str | None = None
    mode: TravelMode | None = None


class TravelStop(Contract):
    place: Destination
    suggested_start: time
    suggested_minutes: int
    amap_details: AmapPoiDetail | None
    access: list[VisitSource] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)


class ModeComparison(Contract):
    selected_route_id: UUID
    alternatives: list[TravelRoute]
    segments_by_route: dict[UUID, list[MapSegment]] = Field(default_factory=dict)


class TravelDay(Contract):
    on_date: date | None
    start_time: time = time(9)
    stops: list[TravelStop]
    connections: list[ModeComparison]
    time_budget_minutes: int
    elapsed_minutes: int
    travel_seconds: int
    suggested_stay_minutes: int


class TravelPlan(Contract):
    anchor: Destination
    preferences: TravelPreferences
    areas: list[VisitArea]
    days: list[TravelDay]
    unscheduled: list[Destination]
    feasibility: Literal["partial", "conditional"]
    gaps: list[PlanningGap]
    evidence: list[Evidence]

    def routes_for(self, task_id: UUID) -> TravelRoutesRead:
        return TravelRoutesRead(
            task_id=task_id,
            routes=[
                route
                for day in self.days
                for comparison in day.connections
                for route in comparison.alternatives
                if route.route_id == comparison.selected_route_id
            ],
        )


_ROUTE_GAPS = {
    "no_route": "该交通方式没有可用路线",
    "missing_citycode": "起终点缺少高德城市码，无法查询公交",
    "missing_metrics": "高德未返回完整的路线距离或耗时",
}


@dataclass(frozen=True)
class _Visit:
    place: Destination
    start: int
    stay: int

    @property
    def end(self) -> int:
        return self.start + self.stay


def _local_time(minute: int) -> time:
    return time(minute // 60, minute % 60)


class TravelPlanner:
    def __init__(
        self, places: AmapPlaces, routes: AmapRoutes, *, notices: ResearchEngine | None = None
    ):
        self._places = places
        self._routes = routes
        self._notices = notices

    async def invoke(self, request: ExpertSkillInvocation) -> ExpertSkillResult:
        def fail(code: str, message: str, *, retryable: bool = False) -> ExpertSkillResult:
            return ExpertSkillResult(
                invocation_id=request.invocation_id,
                status="failed",
                error=ErrorInfo(code=code, message=message, retryable=retryable),
            )

        if request.agent_id != TRAVEL_AGENT_ID or request.skill != TRAVEL_PROFILE.skills[0]:
            return fail("SKILL_NOT_AUTHORIZED", "当前专家或技能版本无权执行旅游规划")
        try:
            data = TravelSkillInput.model_validate(request.input)
        except ValidationError:
            return fail("INVALID_SKILL_INPUT", "旅游规划需要经校验的已确认目的地及问卷答案")
        try:
            async with asyncio.timeout(TRAVEL_SKILL.budget.model.timeout_seconds):
                result = await self.plan(data.intake, evidence=data.evidence)
        except RunError as exc:
            return ExpertSkillResult(
                invocation_id=request.invocation_id, status="failed", error=exc.info
            )
        except TimeoutError:
            return fail("TIMEOUT", "旅游规划查询超时", retryable=True)
        if result is None:
            return fail("SKILL_NOT_AUTHORIZED", "用户未选择旅游路线")
        return ExpertSkillResult(
            invocation_id=request.invocation_id,
            status="completed",
            output=result.model_dump(mode="json"),
        )

    async def plan(
        self,
        intake: ResearchIntakeContext,
        *,
        evidence: Sequence[Evidence] = (),
    ) -> TravelPlan | None:
        if not intake.travel_route_requested:
            return None
        anchor = intake.destination
        prefs = intake.travel_preferences or TravelPreferences()
        modes = prefs.transport_modes or ["walking", "transit"]
        minutes, max_stops = DAILY_BUDGET[prefs.intensity or "balanced"]
        number_of_days, first_date, dates_conflict = trip_window(prefs)
        gaps = []
        if dates_conflict:
            gaps.append(
                PlanningGap(
                    code="date_window", message="停留天数与所选日期不符，采用日期允许的范围"
                )
            )
        if prefs.budget_cny is not None:
            gaps.append(
                PlanningGap(code="price_unverified", message="整团预算保留，实际票价和总费用须核对")
            )
        if "driving" in modes:
            gaps.append(
                PlanningGap(
                    code="driving_restrictions_unverified",
                    message="请按车型、车牌和出行日期核验限行",
                )
            )
        if set(prefs.companions or []) & {"child", "older_adult", "wheelchair"}:
            gaps.append(
                PlanningGap(
                    code="accessibility_unverified", message="请核对接驳、步行台阶及无障碍条件"
                )
            )
            max_stops = min(max_stops, 2)

        administrative = anchor.administrative
        search_region = administrative.citycode or administrative.city or administrative.adcode
        places = [anchor]
        if search_region:
            nearby = await self._places.search(
                "景点", region=search_region, types="110000", limit=MAX_VISITS
            )
            places.extend(place for place in nearby if place.poi_id != anchor.poi_id)
        else:
            gaps.append(
                PlanningGap(
                    code="region_unverified", message="高德未提供可用于限定景点搜索的城市或区县"
                )
            )
        if len(places) == 1 and search_region:
            gaps.append(
                PlanningGap(
                    code="no_additional_poi", message="高德在确认地区未找到可组合的其他景点"
                )
            )
        places = places[:MAX_VISITS]
        details = dict(
            zip(
                (place.poi_id for place in places),
                await asyncio.gather(*(self._detail(place) for place in places)),
                strict=True,
            )
        )
        for place in places:
            detail, error = details[place.poi_id]
            if error:
                gaps.append(PlanningGap(**error.model_dump(), poi_id=place.poi_id))
            elif detail is None:
                gaps.append(
                    PlanningGap(
                        code="poi_detail_missing", message="高德未返回景点详情", poi_id=place.poi_id
                    )
                )

        days = []
        remaining = list(places)
        sources = {item.evidence_id: item for item in evidence}
        used: dict[UUID, Evidence] = {}
        stays = {place.poi_id: stay_minutes(place, details[place.poi_id][0]) for place in places}
        end_minute = START_MINUTE + minutes
        for index in range(min(number_of_days, MAX_VISITS)):
            if not remaining:
                break
            on_date = first_date + timedelta(days=index) if first_date else None
            candidates = []
            for place in remaining:
                stay = stays[place.poi_id]
                start = next_visit_start(details[place.poi_id][0], on_date, START_MINUTE, stay)
                if start is None or start + stay > end_minute:
                    gaps.append(
                        PlanningGap(
                            code="outside_open_hours",
                            message="当天开放时段无法容纳建议停留时间",
                            poi_id=place.poi_id,
                        )
                    )
                else:
                    candidates.append(_Visit(place, start, stay))
            if not candidates:
                continue
            if index:
                gaps.append(
                    PlanningGap(
                        code="day_start_unverified",
                        message=f"第 {index + 1} 天从首站计时，住宿到首站耗时未知",
                    )
                )
            selected = [candidates[0]]
            remaining.remove(candidates[0].place)
            transit_seconds = 0
            connections = []
            chosen_modes = set(modes)
            while len(selected) < max_stops:
                available = [visit.place for visit in candidates if visit.place in remaining]
                if not available:
                    break
                previous = selected[-1]
                start = previous.place
                at_time = _local_time(previous.end)
                options = []
                for candidate in shortlist(start, available, prefs):
                    alternatives, leg_gaps, segments = await self._compare(
                        start, candidate, modes, on_date, at_time
                    )
                    gaps.extend(leg_gaps)
                    if not alternatives:
                        continue
                    fastest = next(
                        (route for route in alternatives if route.mode in chosen_modes), None
                    )
                    if fastest is None:
                        gaps.append(
                            PlanningGap(
                                code="vehicle_continuity",
                                message="没有可接续上一段的交通方式",
                                origin_poi_id=start.poi_id,
                                poi_id=candidate.poi_id,
                            )
                        )
                        continue
                    candidate_stay = stays[candidate.poi_id]
                    arrival = previous.end + (fastest.duration_seconds + 59) // 60
                    start_minute = next_visit_start(
                        details[candidate.poi_id][0],
                        on_date,
                        arrival,
                        candidate_stay,
                    )
                    if start_minute is None:
                        gaps.append(
                            PlanningGap(
                                code="outside_open_hours",
                                message="抵达时已无足够的开放时段",
                                origin_poi_id=start.poi_id,
                                poi_id=candidate.poi_id,
                            )
                        )
                        continue
                    visit = _Visit(candidate, start_minute, candidate_stay)
                    if visit.end > end_minute:
                        gaps.append(
                            PlanningGap(
                                code="time_budget_exceeded",
                                message="通勤和游览时间超过当天预算",
                                origin_poi_id=start.poi_id,
                                poi_id=candidate.poi_id,
                            )
                        )
                        continue
                    options.append((visit, fastest, alternatives, segments))
                if not options:
                    break
                visit, route, alternatives, segments = min(
                    options,
                    key=lambda item: (item[0].start, item[1].duration_seconds, item[1].distance_m),
                )
                selected.append(visit)
                remaining.remove(visit.place)
                transit_seconds += route.duration_seconds
                # A car or a bike parked at the last stop must remain available for the next leg.
                chosen_modes = (
                    {route.mode} if route.mode in PERSONAL_MODES else chosen_modes - PERSONAL_MODES
                )
                connections.append(
                    ModeComparison(
                        selected_route_id=route.route_id,
                        alternatives=alternatives,
                        segments_by_route=segments,
                    )
                )
                map_source = route_evidence(route, on_date)
                used[map_source.evidence_id] = map_source
            stops = []
            for visit in selected:
                place = visit.place
                detail = details[place.poi_id][0]
                access = await self._access(place, sources, gaps)
                used.update((item.evidence_id, sources[item.evidence_id]) for item in access)
                # The source must still be checked for the specific date and traveler.
                stops.append(
                    TravelStop(
                        place=place,
                        suggested_start=_local_time(visit.start),
                        suggested_minutes=visit.stay,
                        amap_details=detail,
                        access=access,
                        pending_checks=["预约与名额", "当日开放时间", "出行限制与门票"],
                    )
                )
            days.append(
                TravelDay(
                    on_date=on_date,
                    stops=stops,
                    connections=connections,
                    time_budget_minutes=minutes,
                    elapsed_minutes=selected[-1].end - START_MINUTE,
                    travel_seconds=transit_seconds,
                    suggested_stay_minutes=sum(visit.stay for visit in selected),
                )
            )
        if remaining:
            gaps.append(PlanningGap(code="unscheduled_poi", message="部分景点未能安排在本次行程内"))
        if len(days) < number_of_days:
            gaps.append(
                PlanningGap(code="unplanned_days", message="已核对的景点不足以覆盖全部停留天数")
            )
        has_connections = any(day.connections for day in days)
        if not has_connections:
            gaps.append(
                PlanningGap(code="no_linked_itinerary", message="当前没有具备高德路段的多景点行程")
            )
        return TravelPlan(
            anchor=anchor,
            preferences=prefs,
            areas=group_areas(places),
            days=days,
            unscheduled=remaining,
            feasibility="partial"
            if remaining or len(days) < number_of_days or not has_connections
            else "conditional",
            gaps=list({gap.model_dump_json(): gap for gap in gaps}.values()),
            evidence=list(used.values()),
        )

    async def _detail(self, place: Destination) -> tuple[AmapPoiDetail | None, ErrorInfo | None]:
        try:
            return await self._places.detail(place.poi_id), None
        except RunError as exc:
            if exc.info.code == "AMAP_NOT_CONFIGURED":
                raise
            return None, exc.info

    async def _compare(
        self,
        start: Destination,
        end: Destination,
        modes: Sequence[TravelMode],
        on_date: date | None,
        at_time: time,
    ) -> tuple[list[TravelRoute], list[PlanningGap], dict[UUID, list[MapSegment]]]:
        async def for_mode(mode: TravelMode):
            leg = {"origin_poi_id": start.poi_id, "poi_id": end.poi_id, "mode": mode}
            try:
                result = await self._routes.route(
                    start,
                    end,
                    mode,
                    on_date=on_date,
                    at_time=at_time if on_date else None,
                )
            except RunError as exc:
                return [], [PlanningGap(**exc.info.model_dump(), **leg)], {}
            gaps = []
            if result.gap:
                gaps.append(PlanningGap(code=result.gap, message=_ROUTE_GAPS[result.gap], **leg))
            if result.restricted_route_ids:
                gaps.append(
                    PlanningGap(
                        code="amap_route_restricted",
                        message="高德返回限行路段，已从行程候选中排除",
                        **leg,
                    )
                )
            routes = [
                route
                for route in result.routes
                if route.route_id not in result.restricted_route_ids
            ]
            segments = {
                route.route_id: result.segments_by_route[route.route_id] for route in routes
            }
            return routes, gaps, segments

        results = await asyncio.gather(*(for_mode(mode) for mode in modes))
        return (
            sorted(
                (route for routes, _, _ in results for route in routes),
                key=lambda route: (route.duration_seconds, route.distance_m),
            ),
            [gap for _, gaps, _ in results for gap in gaps],
            {route_id: parts for _, _, entries in results for route_id, parts in entries.items()},
        )

    async def _access(
        self, place: Destination, sources: dict[UUID, Evidence], gaps: list[PlanningGap]
    ) -> list[VisitSource]:
        access = access_sources(place, list(sources.values()))
        if self._notices is None or len({item.kind for item in access}) >= 4:
            return access
        city = place.administrative.city or place.administrative.district or ""
        result = await self._notices.search(
            f"{city} {place.name} 官方 预约 开放时间 门票 限制",
            sources=("bocha",),
            limit=3,
        )
        for source in result.evidence:
            sources.setdefault(source.evidence_id, source)
        gaps.extend(
            PlanningGap(
                code=error.code,
                message=error.message,
                retryable=error.retryable,
                poi_id=place.poi_id,
            )
            for error in result.errors
        )
        return access_sources(place, list(sources.values()))
