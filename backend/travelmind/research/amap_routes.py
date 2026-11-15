from dataclasses import dataclass
from datetime import date, datetime, time
from math import cos, hypot, radians
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from travelmind.runtime.execution import RunError
from travelmind.runtime.travel.map_cache import AmapResultCache, invalid_response
from travelmind.runtime.travel.places import parse_point, point_parameter
from travelmind.schemas.domain import Contract
from travelmind.schemas.map_data import MapSegment, MapTransitStop
from travelmind.schemas.phase4_intake import Destination, Gcj02Point, TravelMode
from travelmind.schemas.phase4_sources import TravelRoute

ROUTE_TTL = 300
MAX_POLYLINE_JOIN_METERS = 10
ROUTE_PATHS = {
    "driving": "driving",
    "transit": "transit/integrated",
    "walking": "walking",
    "cycling": "bicycling",
}


class RouteOptions(Contract):
    routes: list[TravelRoute] = Field(default_factory=list)
    gap: Literal["no_route", "missing_metrics", "missing_citycode"] | None = None
    restricted_route_ids: list[UUID] = Field(default_factory=list)
    segments_by_route: dict[UUID, list[MapSegment]] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Measurement:
    index: int
    distance: int
    duration: int
    polyline: list[Gcj02Point] | None
    segments: list[MapSegment]
    restricted: bool


@dataclass(frozen=True)
class _RouteSnapshot:
    fetched_at: datetime
    measurements: list[_Measurement]
    gap: Literal["no_route", "missing_metrics"] | None


def _options(payload: dict, field: str) -> list[dict]:
    route = payload.get("route")
    if route in (None, []) and str(payload.get("count")) == "0":
        return []
    if not isinstance(route, dict):
        raise invalid_response()
    options = route.get(field)
    if options is None and str(payload.get("count")) == "0":
        return []
    if not isinstance(options, list) or not all(isinstance(item, dict) for item in options):
        raise invalid_response()
    return options


def _objects(value: Any) -> list[dict]:
    return (
        value if isinstance(value, list) and all(isinstance(item, dict) for item in value) else []
    )


def _text(value: Any) -> str | None:
    return value.strip()[:200] or None if isinstance(value, str) else None


def _transit_stop(value: Any) -> MapTransitStop | None:
    if isinstance(value, dict) and (name := _text(value.get("name"))):
        return MapTransitStop(
            stop_id=_text(value.get("id")),
            name=name,
            coordinates=parse_point(value.get("location")),
        )
    return None


def _path(value: Any) -> list[Gcj02Point] | None:
    raw = value.get("polyline") if isinstance(value, dict) else value
    if not isinstance(raw, str) or raw.count(";") >= 10_000:
        return None
    points = []
    for item in raw.strip(";").split(";"):
        point = parse_point(item)
        if point is None:
            return None
        if not points or point != points[-1]:
            points.append(point)
    return points if len(points) >= 2 else None


def _part(raw: dict, mode: str) -> MapSegment:
    cost = raw.get("cost")
    duration = _metric(cost.get("duration") if isinstance(cost, dict) else None)
    return MapSegment(
        mode=mode,
        line_id=_text(raw.get("id")) if mode in ("bus", "railway") else None,
        name=_text(raw.get("name") or raw.get("road_name") or raw.get("road")),
        departure_stop=_transit_stop(raw.get("departure_stop")),
        arrival_stop=_transit_stop(raw.get("arrival_stop")),
        distance_m=_metric(raw.get("step_distance", raw.get("distance"))),
        duration_seconds=duration if duration is not None else _metric(raw.get("duration")),
        path=_path(raw.get("polyline")),
    )


def _transit_parts(segment: dict) -> list[MapSegment]:
    parts = []
    for key in ("walking", "bus", "railway", "taxi"):
        value = segment.get(key)
        if not isinstance(value, dict) or not value:
            continue
        if key == "walking":
            steps = _objects(value.get("steps"))
            if steps or _metric(value.get("distance")) != 0:
                parts.extend(_part(step, key) for step in steps or [value])
        elif key == "bus":
            lines = _objects(value.get("buslines"))
            parts.append(_part(lines[0] if lines else value, key))
        else:
            parts.append(_part(value, key))
    return parts


def _join_distance_m(left: Gcj02Point, right: Gcj02Point) -> float:
    dlat = left.latitude - right.latitude
    dlon = abs(left.longitude - right.longitude)
    longitude_scale = cos(radians((left.latitude + right.latitude) / 2))
    return 111_195 * hypot(dlat, min(dlon, 360 - dlon) * longitude_scale)


def _segments(option: dict, mode: TravelMode) -> list[MapSegment]:
    if mode == "transit" and (sections := _objects(option.get("segments"))):
        return [
            part
            for section in sections
            for part in _transit_parts(section) or [_part(section, mode)]
        ]
    return [_part(step, mode) for step in _objects(option.get("steps")) or [option]]


def _polyline(segments: list[MapSegment]) -> list[Gcj02Point] | None:
    # Render a single line only when each pair of adjacent provider parts connects.
    points: list[Gcj02Point] = []
    for segment in segments:
        piece = segment.path
        if piece is None or len(points) + len(piece) > 10_000:
            return None
        if points and _join_distance_m(points[-1], piece[0]) > MAX_POLYLINE_JOIN_METERS:
            return None
        points.extend(piece[1:] if points and points[-1] == piece[0] else piece)
    return points if len(points) >= 2 else None


def _metric(value: Any) -> int | None:
    text = str(value)
    if text.isascii() and text.isdecimal() and len(text) <= 12:
        return int(text)
    return None


def _route_snapshot(payload: dict, fetched_at: datetime, *, mode: TravelMode) -> _RouteSnapshot:
    options = _options(payload, "transits" if mode == "transit" else "paths")
    if not options:
        return _RouteSnapshot(fetched_at, [], "no_route")
    measurements = []
    for index, option in enumerate(options):
        cost = option.get("cost")
        duration = _metric(cost.get("duration") if isinstance(cost, dict) else None)
        if duration is None:
            duration = _metric(option.get("duration"))
        distance = _metric(option.get("distance"))
        if duration is None or distance is None:
            continue
        segments = _segments(option, mode)
        measurements.append(
            _Measurement(
                index=index,
                distance=distance,
                duration=duration,
                polyline=_polyline(segments),
                segments=segments,
                restricted=mode == "driving" and str(option.get("restriction")) == "1",
            )
        )
    gap = "missing_metrics" if len(measurements) < len(options) else None
    return _RouteSnapshot(fetched_at, measurements, gap)


class AmapRoutes:
    def __init__(self, cache: AmapResultCache):
        self._cache = cache

    async def route(
        self,
        origin: Destination,
        destination: Destination,
        mode: TravelMode,
        *,
        on_date: date | None = None,
        at_time: time | None = None,
        strategy: int | None = None,
    ) -> RouteOptions:
        params = {
            "origin": point_parameter(origin.coordinates),
            "destination": point_parameter(destination.coordinates),
            "show_fields": "cost,polyline",
        }
        if mode == "transit":
            city1, city2 = origin.administrative.citycode, destination.administrative.citycode
            if not city1 or not city2:
                return RouteOptions(gap="missing_citycode")
            params.update(
                city1=city1, city2=city2, originpoi=origin.poi_id, destinationpoi=destination.poi_id
            )
            if on_date:
                params["date"] = on_date.isoformat()
            if at_time:
                params["time"] = at_time.strftime("%H-%M")
        elif mode in ("driving", "walking"):
            params.update(origin_id=origin.poi_id, destination_id=destination.poi_id)
        if strategy is not None:
            if mode not in ("driving", "transit"):
                raise ValueError("strategy requires driving or transit")
            params["strategy"] = str(strategy)
        path = f"/v5/direction/{ROUTE_PATHS[mode]}"
        try:
            snapshot = await self._cache.request(
                path,
                params,
                ttl=ROUTE_TTL,
                parse=lambda payload, fetched_at: _route_snapshot(payload, fetched_at, mode=mode),
            )
        except RunError as exc:
            if exc.info.code == "AMAP_NO_ROUTE":
                return RouteOptions(gap="no_route")
            raise
        result = RouteOptions(gap=snapshot.gap)
        for measure in snapshot.measurements:
            route_id = uuid5(
                NAMESPACE_URL,
                repr(
                    (
                        "amap",
                        path,
                        sorted(params.items()),
                        origin.poi_id,
                        destination.poi_id,
                        snapshot.fetched_at.isoformat(),
                        measure.index,
                        measure.distance,
                        measure.duration,
                    )
                ),
            )
            result.routes.append(
                TravelRoute(
                    route_id=route_id,
                    origin=origin,
                    destination=destination,
                    mode=mode,
                    fetched_at=snapshot.fetched_at,
                    duration_seconds=measure.duration,
                    distance_m=measure.distance,
                    polyline=measure.polyline,
                )
            )
            if measure.restricted:
                result.restricted_route_ids.append(route_id)
            result.segments_by_route[route_id] = measure.segments
        return result
