import re
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, ValidationError

from travelmind.runtime.execution import RunError
from travelmind.runtime.travel.map_cache import AmapResultCache, invalid_response
from travelmind.schemas.domain import Contract
from travelmind.schemas.phase4_intake import AdministrativeDivisions, Destination, Gcj02Point

POI_TTL = 900


class GeocodedAddress(Contract):
    address: str
    coordinates: Gcj02Point
    administrative: AdministrativeDivisions
    fetched_at: AwareDatetime


class AmapPoiDetail(Contract):
    place: Destination
    fetched_at: AwareDatetime
    category: str | None = None
    phone: str | None = None
    opening_hours: str | None = None
    opening_hours_today: str | None = None
    entrance: Gcj02Point | None = None
    exit: Gcj02Point | None = None


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def parse_point(value: Any) -> Gcj02Point | None:
    if not isinstance(value, str):
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        return Gcj02Point(longitude=float(parts[0]), latitude=float(parts[1]))
    except ValueError:
        return None


def point_parameter(point: Gcj02Point) -> str:
    return f"{point.longitude:.6f},{point.latitude:.6f}"


def _divisions(raw: dict, *, names: tuple[str, str, str]) -> AdministrativeDivisions:
    adcode = _text(raw.get("adcode")) or ""
    citycode = _text(raw.get("citycode")) or ""
    return AdministrativeDivisions(
        province=(_text(raw.get(names[0])) or "")[:200] or None,
        city=(_text(raw.get(names[1])) or "")[:200] or None,
        district=(_text(raw.get(names[2])) or "")[:200] or None,
        adcode=adcode if re.fullmatch(r"[0-9]{6}", adcode) else None,
        citycode=citycode if re.fullmatch(r"[0-9]{3,4}", citycode) else None,
    )


def _place(raw: dict) -> Destination | None:
    poi_id, name = _text(raw.get("id")), _text(raw.get("name"))
    point = parse_point(raw.get("location"))
    if not poi_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", poi_id) or not name or point is None:
        return None
    try:
        return Destination(
            poi_id=poi_id,
            name=name,
            coordinates=point,
            address=(_text(raw.get("address")) or "")[:200] or None,
            administrative=_divisions(raw, names=("pname", "cityname", "adname")),
        )
    except ValidationError:
        return None


def _items(payload: dict, key: str) -> list[dict]:
    items = payload.get(key, [] if str(payload.get("count")) == "0" else None)
    if not isinstance(items, list):
        raise invalid_response()
    return [item for item in items if isinstance(item, dict)]


def _regeocode(payload: dict) -> dict:
    raw = payload.get("regeocode")
    if not isinstance(raw, dict):
        raise invalid_response()
    return raw


def _search_results(payload: dict, _: datetime) -> list[Destination]:
    places = {}
    for raw in _items(payload, "pois"):
        if place := _place(raw):
            places.setdefault(place.poi_id, place)
    return list(places.values())


def _detail_result(payload: dict, fetched_at: datetime, poi_id: str) -> AmapPoiDetail | None:
    for raw in _items(payload, "pois"):
        if raw.get("id") != poi_id or (place := _place(raw)) is None:
            continue
        business = raw["business"] if isinstance(raw.get("business"), dict) else {}
        navi = raw["navi"] if isinstance(raw.get("navi"), dict) else {}
        return AmapPoiDetail(
            place=place,
            fetched_at=fetched_at,
            category=_text(raw.get("type")),
            phone=_text(business.get("tel")),
            opening_hours=_text(business.get("opentime_week")),
            opening_hours_today=_text(business.get("opentime_today")),
            entrance=parse_point(navi.get("entr_location")),
            exit=parse_point(navi.get("exit_location")),
        )
    return None


def _geocoded_addresses(payload: dict, fetched_at: datetime) -> list[GeocodedAddress]:
    return [
        GeocodedAddress(
            address=label,
            coordinates=point,
            fetched_at=fetched_at,
            administrative=_divisions(raw, names=("province", "city", "district")),
        )
        for raw in _items(payload, "geocodes")
        if (point := parse_point(raw.get("location"))) is not None
        and (label := _text(raw.get("formatted_address")))
    ]


def _reverse_result(
    payload: dict,
    fetched_at: datetime,
    point: Gcj02Point,
) -> GeocodedAddress | None:
    raw = _regeocode(payload)
    label = _text(raw.get("formatted_address"))
    if label is None:
        return None
    administrative = raw.get("addressComponent")
    return GeocodedAddress(
        address=label,
        coordinates=point,
        fetched_at=fetched_at,
        administrative=_divisions(
            administrative if isinstance(administrative, dict) else {},
            names=("province", "city", "district"),
        ),
    )


class AmapPlaces:
    def __init__(self, cache: AmapResultCache):
        self._cache = cache

    async def search(
        self,
        keywords: str,
        *,
        region: str | None = None,
        types: str | None = None,
        limit: int = 10,
    ) -> list[Destination]:
        query = keywords.strip()
        if not query or len(query) > 80:
            raise RunError(
                "AMAP_INVALID_KEYWORD", "请输入 1–80 个字符的地点关键词，可用城市或区县缩小范围"
            )
        if not 1 <= limit <= 25:
            raise ValueError("Amap page size must be between 1 and 25")
        params = {"keywords": query, "page_size": str(limit)}
        if region:
            params.update(region=region, city_limit="true")
        if types:
            params["types"] = types
        places = await self._cache.request(
            "/v5/place/text",
            params,
            ttl=POI_TTL,
            parse=_search_results,
        )
        return places[:limit]

    async def detail(self, poi_id: str) -> AmapPoiDetail | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", poi_id):
            raise RunError("AMAP_INVALID_POI_ID", "请输入单个有效的高德 POI ID")
        return await self._cache.request(
            "/v5/place/detail",
            {"id": poi_id, "show_fields": "business,navi"},
            ttl=POI_TTL,
            parse=lambda payload, fetched_at: _detail_result(payload, fetched_at, poi_id),
        )

    async def geocode(self, address: str, *, city: str | None = None) -> list[GeocodedAddress]:
        address = address.strip()
        if not address:
            raise RunError("AMAP_INVALID_ADDRESS", "请输入需要解析的地址")
        params = {"address": address}
        if city:
            params["city"] = city
        return await self._cache.request(
            "/v3/geocode/geo",
            params,
            ttl=POI_TTL,
            parse=_geocoded_addresses,
        )

    async def reverse_geocode(self, point: Gcj02Point) -> GeocodedAddress | None:
        query_point = parse_point(point_parameter(point))
        assert query_point is not None
        return await self._cache.request(
            "/v3/geocode/regeo",
            {"location": point_parameter(point)},
            ttl=POI_TTL,
            parse=lambda payload, fetched_at: _reverse_result(payload, fetched_at, query_point),
        )
