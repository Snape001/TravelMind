import asyncio
import logging
from datetime import UTC, date, datetime, time

import httpx
import pytest

from travelmind.research.amap_routes import AmapRoutes
from travelmind.runtime.execution import RunError
from travelmind.runtime.travel.map_cache import AmapResultCache
from travelmind.runtime.travel.places import AmapPlaces
from travelmind.schemas.phase4_intake import Destination, Gcj02Point


@pytest.fixture
def anyio_backend():
    return "asyncio"


def poi(poi_id="B0001", location="118.582374,24.910773", **extra):
    return {
        "id": poi_id,
        "name": "开元寺",
        "cityname": "泉州市",
        "adname": "鲤城区",
        "adcode": "350502",
        "citycode": "0595",
        "pname": "福建省",
        "location": location,
        "address": [],
        **extra,
    }


def destination(poi_id="B0001", longitude=118.582374, citycode="0595"):
    return Destination(
        poi_id=poi_id,
        name=poi_id,
        coordinates=Gcj02Point(longitude=longitude, latitude=24.910773),
        administrative={"citycode": citycode},
    )


def ok(**data):
    return httpx.Response(200, json={"status": "1", "infocode": "10000", **data})


class StubTransport:
    """Represents the transport shared with the intake module."""

    def __init__(self, http, key="test-key"):
        self.http = http
        self.key = key

    async def get(self, path, **params):
        if not self.key:
            raise RunError("AMAP_NOT_CONFIGURED", "高德地点服务尚未配置 API Key")
        response = await self.http.get(
            f"https://restapi.amap.com{path}", params={**params, "key": self.key}
        )
        payload = response.json()
        if payload["status"] == "0":
            raise RunError("AMAP_RATE_LIMIT", "高德服务请求已达限额", retryable=True)
        return payload


@pytest.mark.anyio
async def test_search_filters_and_preserves_provider_poi():
    def handle(request):
        assert request.url.path == "/v5/place/text"
        assert request.url.params["keywords"] == "开元寺"
        assert request.url.params["region"] == "350502"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["key"] == "test-key"
        return ok(pois=[poi(), poi(location=[]), poi(poi_id=[]), poi("B0002", "broken"), poi()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        places = AmapPlaces(AmapResultCache(StubTransport(http)))
        (item,) = await places.search(" 开元寺 ", region="350502")
        assert item.poi_id == "B0001"
        assert item.coordinates == Gcj02Point(longitude=118.582374, latitude=24.910773)
        assert item.address is None
        assert item.administrative.citycode == "0595"
        assert item.administrative.adcode == "350502"
        with pytest.raises(RunError) as error:
            await places.search("长" * 81)
        assert "80" in error.value.info.message


@pytest.mark.anyio
async def test_poi_details_and_geocodes():
    def handle(request):
        if request.url.path == "/v5/place/detail":
            assert request.url.params["id"] == "B0001"
            return ok(
                pois=[poi("OTHER"), poi(business={"opentime_week": "09:00-17:00", "cost": "35"})]
            )
        if request.url.path == "/v3/geocode/geo":
            assert request.url.params["city"] == "350502"
            return ok(
                geocodes=[
                    {
                        "location": "118.582374,24.910773",
                        "formatted_address": "泉州开元寺",
                        "citycode": "0595",
                    }
                ]
            )
        assert request.url.path == "/v3/geocode/regeo"
        assert request.url.params["location"] == "118.582374,24.910773"
        return ok(
            regeocode={"formatted_address": "泉州开元寺", "addressComponent": {"citycode": "0595"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        places = AmapPlaces(AmapResultCache(StubTransport(http)))
        detail = await places.detail("B0001")
        assert detail.place.poi_id == "B0001"
        assert detail.opening_hours == "09:00-17:00"
        assert detail.model_dump().get("ticket_price") is None
        (match,) = await places.geocode("泉州开元寺", city="350502")
        assert match.coordinates == detail.place.coordinates
        assert match.model_dump().get("poi_id") is None
        assert (await places.reverse_geocode(match.coordinates)).address == "泉州开元寺"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,path",
    [
        ("driving", "driving"),
        ("walking", "walking"),
        ("cycling", "bicycling"),
        ("transit", "transit/integrated"),
    ],
)
async def test_routes_use_provider_measurements_and_polyline(mode, path):
    shape = "118.582374,24.910773;118.593411,24.914588"

    def handle(request):
        assert request.url.path == f"/v5/direction/{path}"
        assert request.url.params["origin"] == "118.582374,24.910773"
        assert request.url.params["show_fields"] == "cost,polyline"
        if mode == "transit":
            assert request.url.params["city1"] == request.url.params["city2"] == "0595"
            assert request.url.params["date"] == "2026-12-03"
            assert request.url.params["time"] == "09-05"
            return ok(
                route={
                    "transits": [
                        {
                            "distance": "533",
                            "cost": {"duration": "306"},
                            "segments": [{"bus": {"buslines": [{"polyline": shape}]}}],
                        }
                    ]
                }
            )
        return ok(
            route={
                "paths": [
                    {"distance": "533", "cost": {"duration": "306"}, "steps": [{"polyline": shape}]}
                ]
            }
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        routes = AmapRoutes(AmapResultCache(StubTransport(http)))
        result = await routes.route(
            destination(),
            destination("B0002", 118.593411),
            mode,
            on_date=date(2026, 12, 3),
            at_time=time(9, 5),
        )
        (leg,) = result.routes
        assert leg.distance_m == 533 and leg.duration_seconds == 306
        assert leg.mode == mode
        assert leg.polyline[-1].longitude == 118.593411
        assert leg.fetched_at.tzinfo is not None


@pytest.mark.anyio
async def test_no_transit_and_missing_metrics_are_distinct_gaps():
    replies = iter([ok(count="0"), ok(route={"paths": [{"distance": "5", "cost": {}}]})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        routes = AmapRoutes(AmapResultCache(StubTransport(http)))
        start, end = destination(), destination("B0002", 118.593411)
        empty = await routes.route(start, end, "transit")
        assert not empty.routes and empty.gap == "no_route"
        incomplete = await routes.route(start, end, "driving")
        assert not incomplete.routes and incomplete.gap == "missing_metrics"
        missing_city = await routes.route(start, destination("B0003", 118.6, None), "transit")
        assert missing_city.gap == "missing_citycode"


@pytest.mark.anyio
async def test_cache_coalesces_queries_and_keeps_original_timestamp():
    calls = 0
    ticks = [0.0]

    async def handle(_):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ok(route={"paths": [{"distance": "22", "cost": {"duration": "6"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = AmapResultCache(
            StubTransport(http),
            monotonic_clock=lambda: ticks[0],
            now=lambda: datetime.fromtimestamp(ticks[0], UTC),
        )
        routes = AmapRoutes(client)
        args = (destination(), destination("B0002", 118.593411), "walking")
        first, second = await asyncio.gather(routes.route(*args), routes.route(*args))
        assert calls == 1 and first == second
        ticks[0] = 1
        assert (await routes.route(*args)) == first
        ticks[0] = 301
        refreshed = await routes.route(*args)
        assert calls == 2 and refreshed.routes[0].fetched_at != first.routes[0].fetched_at


@pytest.mark.anyio
async def test_missing_key_limit_and_invalid_responses_are_safe_and_not_cached(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    calls = 0
    secret = "secret-that-must-stay-server-side"

    def handle(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={"status": "0", "infocode": "10004", "info": secret},
                headers={"Retry-After": "60"},
            )
        if calls == 2:
            return ok(pois=secret)
        return ok(pois=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(RunError) as missing:
            await AmapPlaces(AmapResultCache(StubTransport(http, None))).search("开元寺")
        assert "AMAP_WEB_API_KEY" in missing.value.info.message
        assert calls == 0
        places = AmapPlaces(AmapResultCache(StubTransport(http, secret)))
        for code in ("AMAP_RATE_LIMIT", "AMAP_INVALID_RESPONSE"):
            with pytest.raises(RunError) as error:
                await places.search("开元寺")
            assert error.value.info.code == code
            assert secret not in str(error.value)
        assert await places.search("开元寺") == []
        assert await places.search("开元寺") == []
        assert calls == 3
    assert secret not in caplog.text


@pytest.mark.anyio
async def test_transit_cache_keys_include_local_date_time_strategy_and_direction():
    calls = []

    def handle(request):
        calls.append(request.url)
        return ok(count="0")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        routes = AmapRoutes(AmapResultCache(StubTransport(http)))
        start, end = destination(), destination("B0002", 118.593411)
        for day, when, strategy in (
            (3, time(9), 0),
            (3, time(9), 0),
            (4, time(9), 0),
            (4, time(10), 0),
            (4, time(10), 1),
        ):
            await routes.route(
                start, end, "transit", on_date=date(2026, 12, day), at_time=when, strategy=strategy
            )
        await routes.route(
            end, start, "transit", on_date=date(2026, 12, 4), at_time=time(10), strategy=1
        )
    assert len(set(calls)) == len(calls) == 5


@pytest.mark.anyio
async def test_partial_provider_geometry_and_driving_restrictions_are_reported():
    def handle(_):
        return ok(
            route={
                "paths": [
                    {
                        "distance": "90",
                        "cost": {"duration": "45"},
                        "restriction": "1",
                        "steps": [
                            {"polyline": "118.582374,24.910773;118.583000,24.910773"},
                            {"polyline": "118.590000,24.910773;118.593411,24.910773"},
                        ],
                    }
                ]
            }
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await AmapRoutes(AmapResultCache(StubTransport(http))).route(
            destination(),
            destination("B0002", 118.593411),
            "driving",
        )
    (route,) = result.routes
    assert route.polyline is None
    assert route.distance_m == 90
    assert result.restricted_route_ids == [route.route_id]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["cycling", "transit"])
async def test_live_v5_route_field_variants(mode):
    first = "94.661706,40.128989;94.662357,40.129010"
    second = "94.662354,40.129009;94.662465,40.129084"

    def handle(_):
        if mode == "cycling":
            return ok(
                route={
                    "paths": [
                        {
                            "distance": "6460",
                            "duration": "2099",
                            "steps": [{"polyline": first}, {"polyline": second}],
                        }
                    ]
                }
            )
        return ok(
            route={
                "transits": [
                    {
                        "distance": "4108",
                        "cost": {"duration": "1550"},
                        "segments": [
                            {
                                "walking": {"steps": [{"polyline": {"polyline": first}}]},
                                "bus": {"buslines": [{"polyline": {"polyline": second}}]},
                            }
                        ],
                    }
                ]
            }
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        routes = AmapRoutes(AmapResultCache(StubTransport(http)))
        outcome = await routes.route(destination(), destination("B0002", 118.593411), mode)
    (route,) = outcome.routes
    assert route.duration_seconds == (2099 if mode == "cycling" else 1550)
    assert len(route.polyline) == 4
    assert route.polyline[-1].longitude == 94.662465


@pytest.mark.anyio
async def test_poi_cache_isolated_from_caller_mutation_and_keyed_by_region():
    calls = []

    def handle(request):
        calls.append(request.url.params["region"])
        return ok(pois=[poi()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        places = AmapPlaces(AmapResultCache(StubTransport(http)))
        original = await places.search("开元寺", region="350502")
        original[0].name = "调用者改动"
        assert (await places.search("开元寺", region="350502"))[0].name == "开元寺"
        await places.search("开元寺", region="350503")
    assert calls == ["350502", "350503"]
