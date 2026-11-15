import asyncio
import unicodedata
from datetime import timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from travelmind.research.adapters import text_value
from travelmind.research.engine import ToolResult
from travelmind.research.http import ProviderError
from travelmind.runtime.execution import RunError
from travelmind.runtime.travel.places import AmapPlaces, AmapPoiDetail, point_parameter
from travelmind.schemas.domain import Evidence
from travelmind.schemas.phase4_intake import Destination

CHINA_TIME = timezone(timedelta(hours=8))


def _place_name(name: str) -> str:
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


def _area_region(place: Destination) -> str | None:
    divisions = place.administrative
    if place.name == divisions.city:
        return divisions.citycode or divisions.city
    if place.name == divisions.district:
        return divisions.adcode or divisions.district
    if place.name == divisions.province:
        return divisions.province
    return None


def _evidence(detail: AmapPoiDetail) -> Evidence:
    place = detail.place
    fetched_date = detail.fetched_at.astimezone(CHINA_TIME).date()
    fields = {
        "景点": place.name,
        "类别": detail.category,
        "行政区": " ".join(
            filter(
                None,
                (
                    place.administrative.province,
                    place.administrative.city,
                    place.administrative.district,
                ),
            )
        ),
        "地址": place.address,
        "地点坐标（GCJ-02）": point_parameter(place.coordinates),
        "地图所列联系电话": detail.phone,
        f"地图所列当日营业时间（{fetched_date}）": detail.opening_hours_today,
        "地图所列常规营业时间": detail.opening_hours,
        "入口坐标（GCJ-02）": point_parameter(detail.entrance) if detail.entrance else None,
        "出口坐标（GCJ-02）": point_parameter(detail.exit) if detail.exit else None,
    }
    excerpt = [
        f"{label}：{value[:300]}" for label, raw in fields.items() if (value := text_value(raw))
    ]
    params = urlencode({"poiid": place.poi_id, "src": "TravelMind"})
    return Evidence(
        evidence_id=uuid4(),
        source_type="map",
        source_title=f"高德地图 · {text_value(place.name) or place.poi_id}",
        source_url=f"https://uri.amap.com/marker?{params}",
        source_locator=f"高德地图 POI 详情 · {place.poi_id}",
        retrieved_at=detail.fetched_at,
        excerpt="\n".join(excerpt),
    )


async def research_attractions(
    places: AmapPlaces, destination: str, *, confirmed: Destination | None = None
) -> ToolResult:
    """Collect a confirmed attraction or a small shortlist within a confirmed region."""
    result = ToolResult()
    try:
        anchor = confirmed
        if anchor is None:
            if not 1 <= len(destination.strip()) <= 80:
                return result
            name = _place_name(destination)
            exact = [
                place
                for place in await places.search(destination, limit=6)
                if _place_name(place.name) == name
            ]
            if len(exact) != 1:
                return result
            anchor = exact[0]

        region = _area_region(anchor)
        candidates = (
            await places.search("景点", region=region, types="110000", limit=2)
            if region
            else [anchor]
        )
    except RunError as exc:
        return ToolResult(errors=[ProviderError(provider="amap", **exc.info.model_dump())])

    async def detail(place: Destination) -> AmapPoiDetail | None:
        try:
            return await places.detail(place.poi_id)
        except RunError as exc:
            result.errors.append(ProviderError(provider="amap", **exc.info.model_dump()))
            return None

    results = await asyncio.gather(*(detail(place) for place in candidates))
    result.evidence = [_evidence(item) for item in results if item is not None]
    return result
