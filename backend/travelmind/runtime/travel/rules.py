import re
from collections.abc import Sequence
from datetime import date, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid5

from travelmind.research.sources import is_public_target
from travelmind.runtime.travel.places import AmapPoiDetail
from travelmind.schemas.domain import Contract, Evidence
from travelmind.schemas.phase4_intake import Destination, TravelPreferences
from travelmind.schemas.phase4_sources import TravelRoute

CHINA_TIME = timezone(timedelta(hours=8))
DAILY_BUDGET = {"relaxed": (240, 2), "balanced": (360, 3), "intensive": (480, 4)}
MODE_LABELS = {"walking": "步行", "transit": "公交", "driving": "驾车", "cycling": "骑行"}
STYLE_TERMS = {
    "heritage": "博物馆 古迹 古建筑",
    "nature": "公园 湿地 山 湖",
    "leisure": "公园 街区",
}
ACCESS_TERMS = {
    "reservation": ("预约", "实名", "订票"),
    "opening_hours": ("开放时间", "营业时间", "开馆", "闭馆", "暂停开放"),
    "restrictions": ("禁止", "限行", "限流", "临时关闭", "暂停", "停运"),
    "tickets": ("门票", "票价", "免费开放"),
}


class VisitSource(Contract):
    kind: Literal["reservation", "opening_hours", "restrictions", "tickets"]
    evidence_id: UUID
    quote: str


class VisitArea(Contract):
    city: str | None
    district: str | None
    adcode: str | None
    places: list[Destination]


def region(place: Destination) -> tuple[str | None, str | None, str | None]:
    divisions = place.administrative
    return divisions.city, divisions.district, divisions.adcode


def group_areas(places: Sequence[Destination]) -> list[VisitArea]:
    groups: dict[tuple, list[Destination]] = {}
    for place in places:
        groups.setdefault(region(place), []).append(place)
    return [
        VisitArea(city=city, district=district, adcode=adcode, places=members)
        for (city, district, adcode), members in groups.items()
    ]


def shortlist(
    origin: Destination, candidates: Sequence[Destination], preferences: TravelPreferences
) -> list[Destination]:
    terms = tuple(
        word
        for style in (preferences.styles or [])
        for word in STYLE_TERMS.get(style, style).split()
    )

    def rank(place: Destination) -> tuple:
        # Coordinates only bound fan-out. Provider durations choose the ordered route.
        return (
            bool(terms) and not any(term in place.name for term in terms),
            (place.coordinates.longitude - origin.coordinates.longitude) ** 2
            + (place.coordinates.latitude - origin.coordinates.latitude) ** 2,
        )

    return sorted(candidates, key=rank)[:2]


def stay_minutes(place: Destination, detail: AmapPoiDetail | None) -> int:
    category = (detail.category or "") if detail else ""
    label = f"{place.name} {category}"
    if any(word in label for word in ("博物馆", "动物园", "主题公园", "森林公园")):
        return 120
    if any(word in label for word in ("公园", "景区", "历史街区", "展览馆")):
        return 90
    return 60


def trip_window(preferences: TravelPreferences) -> tuple[int, date | None, bool]:
    start, end, days = preferences.start_date, preferences.end_date, preferences.stay_days
    if start and end:
        window = (end - start).days + 1
        return min(days or window, window), start, days is not None and days != window
    if end and not start:
        max_days = (end - date.min).days + 1
        fit = min(days or 1, max_days)
        return fit, end - timedelta(days=fit - 1), fit != (days or 1)
    if start and days:
        fit = min(days, (date.max - start).days + 1)
        return fit, start, fit != days
    return days or 1, start, False


def next_visit_start(
    detail: AmapPoiDetail | None, on_date: date | None, earliest: int, stay: int
) -> int | None:
    if detail is None or on_date != detail.fetched_at.astimezone(CHINA_TIME).date():
        return earliest
    hours = (detail.opening_hours_today or "").strip("。 ")
    if hours in {
        "休息",
        "今日休息",
        "今日闭馆",
        "闭馆",
        "不营业",
        "暂停营业",
        "暂停开放",
    }:
        return None
    ranges = re.split(r"[\s,，;；]+", hours)
    parsed = [
        re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-4]):([0-5]\d)", item)
        for item in ranges
    ]
    if any(match is None for match in parsed):
        return earliest
    windows = []
    for match in parsed:
        start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
        opening = start_hour * 60 + start_minute
        closing = end_hour * 60 + end_minute
        if not opening < closing <= 1440:
            return earliest
        if max(opening, earliest) + stay <= closing:
            windows.append(max(opening, earliest))
    return min(windows, default=None)


def access_sources(place: Destination, evidence: Sequence[Evidence]) -> list[VisitSource]:
    administrative = place.administrative
    names = [
        name.removesuffix("市")
        for name in (administrative.city, administrative.district)
        if name and len(name.removesuffix("市")) >= 2
    ]
    checks = []
    seen = set()
    for item in evidence:
        if (
            item.source_type != "official"
            or not item.source_url
            or not is_public_target(item.source_url)
        ):
            continue
        if place.name not in item.excerpt:
            continue
        if not names or not any(
            name in item.source_title or name in item.excerpt for name in names
        ):
            continue
        lines = re.split(r"(?<=[。！？；])|\n", item.excerpt)
        named_title = place.name in item.source_title
        for line in map(str.strip, lines):
            if not line or len(line) > 400 or (not named_title and place.name not in line):
                continue
            # Keep the verbatim official wording and date scope for interpretation.
            for kind, terms in ACCESS_TERMS.items():
                key = (kind, line)
                if key not in seen and any(term in line for term in terms):
                    seen.add(key)
                    checks.append(VisitSource(kind=kind, evidence_id=item.evidence_id, quote=line))
            if len(checks) >= 12:
                return checks[:12]
    return checks


def route_evidence(route: TravelRoute, on_date: date | None) -> Evidence:
    label = MODE_LABELS[route.mode]
    return Evidence(
        evidence_id=uuid5(route.route_id, "amap-route-evidence"),
        source_type="map",
        source_title=f"高德地图 · {route.origin.name}至{route.destination.name} · {label}",
        source_locator=f"高德 Web 服务路径规划 2.0 · {route.route_id}",
        retrieved_at=route.fetched_at,
        excerpt=(
            f"高德返回的{label}路段从{route.origin.name}至{route.destination.name}，"
            f"距离 {route.distance_m} 米，预计 {route.duration_seconds} 秒。"
            + (
                f"公交查询日期为 {on_date.isoformat()}。"
                if route.mode == "transit" and on_date
                else ""
            )
        ),
    )
