import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_amap import StubTransport, ok, poi

from travelmind.research.amap_routes import AmapRoutes
from travelmind.runtime.travel.map_cache import AmapResultCache
from travelmind.runtime.travel.places import AmapPlaces
from travelmind.runtime.travel.planner import (
    TRAVEL_PROFILE,
    TRAVEL_SKILL,
    TravelPlanner,
    TravelSkillInput,
    validate_travel_assignment,
)
from travelmind.schemas.domain import Assignment, Evidence, ResearchPlan
from travelmind.schemas.phase4_experts import ExpertSkillInvocation, SkillRef
from travelmind.schemas.phase4_intake import ResearchIntakeContext


@pytest.fixture
def anyio_backend():
    return "asyncio"


def context(*, requested=True, **preferences):
    path = Path(__file__).resolve().parents[2] / "fixtures" / "phase4.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["destination"]["administrative"]["citycode"] = "0595"
    answers = data["research_answers"] if requested else {"answers": []}
    return ResearchIntakeContext(
        destination=data["destination"],
        research_questionnaire=data["research_questionnaire"],
        research_submission=answers,
        travel_route_requested=requested,
        travel_preferences=preferences if requested else None,
    )


def fixture_response(request):
    path = request.url.path
    museum = poi("MUSEUM", "118.584374,24.910773", name="泉州博物馆")
    park = poi("PARK", "118.586374,24.910773", name="滨江公园", adname="丰泽区", adcode="350503")
    if path == "/v5/place/text":
        assert request.url.params["region"] == "0595"
        return ok(pois=[museum, park, museum])
    if path == "/v5/place/detail":
        places = {museum["id"]: museum, park["id"]: park}
        record = places.get(request.url.params["id"])
        return ok(pois=[record] if record else [])
    if path.endswith("/walking"):
        end = request.url.params["destination"]
        return ok(
            route={
                "paths": [
                    {
                        "distance": "440",
                        "cost": {"duration": "600" if end == park["location"] else "1200"},
                    }
                ]
            }
        )
    if path.endswith("/transit/integrated"):
        return ok(count="0")
    raise AssertionError(path)


def expert(http):
    amap = AmapResultCache(StubTransport(http))
    return TravelPlanner(AmapPlaces(amap), AmapRoutes(amap))


def test_planner_gates_route_assignments_using_confirmed_interests():
    travel = Assignment(assignment_id=uuid4(), agent_id="travel_planning", objective="景点路线")
    plan = ResearchPlan(objective="调研", assignments=[travel])
    assert TRAVEL_PROFILE.agent_id == travel.agent_id and TRAVEL_PROFILE.name == "陆知行"
    validate_travel_assignment(context(), plan)
    with pytest.raises(ValueError, match="路线"):
        validate_travel_assignment(context(requested=False), plan)
    plan.assignments[0] = travel.model_copy(update={"agent_id": "tourism_resources"})
    validate_travel_assignment(context(requested=False), plan)
    with pytest.raises(ValueError, match="路线"):
        validate_travel_assignment(context(), plan)


@pytest.mark.anyio
async def test_unchosen_travel_skips_every_provider_request():
    def handle(_):
        pytest.fail("no map request is authorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        assert await expert(http).plan(context(requested=False)) is None


@pytest.mark.anyio
async def test_plan_orders_by_real_route_times_and_retains_map_and_official_evidence():
    official = Evidence(
        evidence_id=uuid4(),
        source_type="official",
        source_title="泉州开元寺参观公告",
        source_url="https://www.quanzhou.gov.cn/notice",
        retrieved_at=datetime.now(UTC),
        excerpt="泉州开元寺参观须提前预约。门票免费。大型活动期间可能临时关闭。",
    )
    denied = official.model_copy(update={"evidence_id": uuid4(), "source_type": "social"})
    same_name = official.model_copy(
        update={
            "evidence_id": uuid4(),
            "source_title": "潮州开元寺公告",
            "excerpt": "潮州开元寺门票 8 元。",
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture_response)) as http:
        result = await expert(http).plan(
            context(transport_modes=["walking", "transit"], budget_cny=0, intensity="balanced"),
            evidence=[official, denied, same_name],
        )
    (day,) = result.days
    assert day.stops[0].place == context().destination
    assert [stop.place.poi_id for stop in day.stops[1:]] == ["PARK", "MUSEUM"]
    assert set(area.district for area in result.areas) == {"鲤城区", "丰泽区"}
    assert day.travel_seconds == 1800
    assert day.stops[1].suggested_minutes > 0
    assert result.preferences.budget_cny == 0 and result.feasibility == "conditional"
    assert len(result.routes_for(uuid4()).routes) == 2
    assert any(gap.code == "no_route" and gap.mode == "transit" for gap in result.gaps)
    assert any(check.evidence_id == official.evidence_id for check in day.stops[0].access)
    assert {item.evidence_id for item in result.evidence if item.source_type == "official"} == {
        official.evidence_id
    }
    assert len([item for item in result.evidence if item.source_type == "map"]) == 2


@pytest.mark.anyio
async def test_impossible_connections_leave_places_unscheduled():
    def handle(request):
        if request.url.path.startswith("/v5/direction/"):
            return ok(route={"paths": [{"distance": "14400", "cost": {"duration": "36000"}}]})
        return fixture_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await expert(http).plan(context(transport_modes=["walking"], intensity="relaxed"))
    assert result.feasibility == "partial"
    assert {place.poi_id for place in result.unscheduled} == {"PARK", "MUSEUM"}
    assert not result.routes_for(uuid4()).routes
    assert any(gap.code == "time_budget_exceeded" for gap in result.gaps)


@pytest.mark.anyio
async def test_skill_checks_agent_and_version_before_running():
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture_response)) as http:
        planner = expert(http)
        invocation = ExpertSkillInvocation(
            invocation_id=uuid4(),
            task_id=uuid4(),
            trace_id=uuid4(),
            run_id=uuid4(),
            skill=SkillRef(skill_id=TRAVEL_SKILL.skill_id, version=TRAVEL_SKILL.version),
            agent_id="travel_planning",
            input=TravelSkillInput(intake=context(transport_modes=["walking"])).model_dump(
                mode="json"
            ),
        )
        bad = await planner.invoke(invocation.model_copy(update={"agent_id": "tourism_resources"}))
        assert bad.status == "failed" and bad.error.code == "SKILL_NOT_AUTHORIZED"
        result = await planner.invoke(invocation)
        assert result.status == "completed" and result.output["days"]


@pytest.mark.anyio
async def test_planner_checks_today_hours_for_the_actual_travel_date():
    today = date(2026, 12, 3)

    def handle(request):
        if request.url.path == "/v5/place/detail":
            poi_id = request.url.params["id"]
            if poi_id == context().destination.poi_id:
                return ok(pois=[poi(poi_id, business={"opentime_today": "10:30-13:00"})])
            if poi_id == "PARK":
                return ok(pois=[poi(poi_id, business={"opentime_today": "今日闭馆"})])
        return fixture_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cache = AmapResultCache(
            StubTransport(http), now=lambda: datetime(2026, 12, 3, 1, tzinfo=UTC)
        )
        planner = TravelPlanner(AmapPlaces(cache), AmapRoutes(cache))
        result = await planner.plan(context(start_date=today, transport_modes=["walking"]))
        assert result.days[0].stops[0].suggested_start == time(10, 30)
        assert [poi.poi_id for poi in result.unscheduled] == ["PARK"]
        assert any(gap.code == "outside_open_hours" for gap in result.gaps)
        tomorrow = await planner.plan(
            context(start_date=date(2026, 12, 4), transport_modes=["walking"])
        )
        assert tomorrow.days[0].stops[0].suggested_start == time(9)
        assert not tomorrow.unscheduled
