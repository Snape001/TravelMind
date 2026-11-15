from datetime import date, time
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from travelmind.schemas.domain import Contract, ShortText, Text
from travelmind.schemas.phase4_intake import (
    Destination,
    Gcj02Point,
    NonnegativeInt,
    PositiveInt,
    TravelMode,
)
from travelmind.schemas.phase4_sources import TravelRoute


class MapTransitStop(Contract):
    stop_id: ShortText | None = None
    name: ShortText
    coordinates: Gcj02Point | None = None


class MapSegment(Contract):
    mode: Literal["driving", "walking", "cycling", "transit", "bus", "railway", "taxi"]
    line_id: ShortText | None = None
    name: ShortText | None = None
    departure_stop: MapTransitStop | None = None
    arrival_stop: MapTransitStop | None = None
    distance_m: NonnegativeInt | None = None
    duration_seconds: NonnegativeInt | None = None
    path: list[Gcj02Point] | None = Field(default=None, min_length=2)


class MapPlace(Contract):
    poi: Destination
    detail_fetched_at: AwareDatetime | None = None
    category: ShortText | None = None


class MapStop(Contract):
    poi_id: ShortText
    suggested_start: time | None = None
    suggested_minutes: PositiveInt


class MapRoute(Contract):
    route: TravelRoute
    evidence_id: UUID | None = None
    segments: list[MapSegment] = Field(default_factory=list)


class MapLeg(Contract):
    selected_route_id: UUID
    alternative_route_ids: list[UUID] = Field(min_length=1)


class MapDay(Contract):
    day_number: PositiveInt
    on_date: date | None = None
    stops: list[MapStop] = Field(min_length=1)
    legs: list[MapLeg] = Field(default_factory=list)


class MapIssue(Contract):
    code: ShortText
    message: Text
    poi_id: ShortText | None = None
    origin_poi_id: ShortText | None = None
    mode: TravelMode | None = None


class MapData(Contract):
    task_id: UUID
    report_id: UUID | None = None
    status: Literal["complete", "partial", "unavailable"]
    destination: Destination | None = None
    places: list[MapPlace] = Field(default_factory=list)
    days: list[MapDay] = Field(default_factory=list)
    routes: list[MapRoute] = Field(default_factory=list)
    issues: list[MapIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        places = {place.poi.poi_id for place in self.places}
        routes = {entry.route.route_id: entry.route for entry in self.routes}
        if len(places) != len(self.places) or len(routes) != len(self.routes):
            raise ValueError("map IDs must be unique")
        if self.destination and self.destination.poi_id not in places:
            raise ValueError("map destination references an unknown POI")
        if any(
            {route.origin.poi_id, route.destination.poi_id} - places for route in routes.values()
        ):
            raise ValueError("map route references an unknown POI")
        used = set()
        for day_number, day in enumerate(self.days, 1):
            if day.day_number != day_number or len(day.legs) != len(day.stops) - 1:
                raise ValueError("map day must have consecutive stops and legs")
            if any(stop.poi_id not in places for stop in day.stops):
                raise ValueError("map stop references an unknown POI")
            for origin, target, leg in zip(day.stops[:-1], day.stops[1:], day.legs, strict=True):
                alternatives = set(leg.alternative_route_ids)
                if (
                    leg.selected_route_id not in alternatives
                    or len(alternatives) != len(leg.alternative_route_ids)
                    or alternatives - routes.keys()
                ):
                    raise ValueError("map leg references an unknown or duplicate route")
                for route_id in alternatives:
                    route = routes[route_id]
                    if (route.origin.poi_id, route.destination.poi_id) != (
                        origin.poi_id,
                        target.poi_id,
                    ):
                        raise ValueError("map route endpoints do not match the leg")
                used.update(alternatives)
        if used != routes.keys():
            raise ValueError("map routes must belong to a day")
        return self

    def selected_routes(self) -> list[TravelRoute]:
        routes = {entry.route.route_id: entry.route for entry in self.routes}
        return [routes[leg.selected_route_id] for day in self.days for leg in day.legs]
