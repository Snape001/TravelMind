import type { components } from "./api.generated";

export type ConfirmedRoute = components["schemas"]["TravelRoute"];
export type MapData = components["schemas"]["MapData"];
export type MapRoute = components["schemas"]["MapRoute"];
type MapSegment = components["schemas"]["MapSegment"];
export type RouteMode = ConfirmedRoute["mode"];
type Place = ConfirmedRoute["origin"];
export type MapPoint = [longitude: number, latitude: number];
export type MapStop = { place: Place; number: number; position: MapPoint; category?: string | null };
type PoiName = { id: string; name: string };
type RouteLeg = { selected: MapRoute; alternatives: MapRoute[] };
type MapDayView = {
  number: number;
  date?: string | null;
  stops: MapStop[];
  schedule: Map<string, components["schemas"]["MapStop"]>;
  legs: RouteLeg[];
};
export type MapView = {
  status: MapData["status"];
  stops: MapStop[];
  days: MapDayView[];
  legs: RouteLeg[];
  routes: MapRoute[];
  issues: NonNullable<MapData["issues"]>;
};

export const modeLabel: Record<RouteMode, string> = {
  driving: "驾车", transit: "公交", walking: "步行", cycling: "骑行",
};
export const segmentLabel: Record<MapSegment["mode"], string> = {
  ...modeLabel, bus: "公交车", railway: "轨道交通", taxi: "出租车",
};

function mapPoint(point: { crs?: string; longitude: number; latitude: number }): MapPoint | null {
  const { longitude, latitude } = point;
  if ((point.crs && point.crs !== "gcj02") || !Number.isFinite(longitude) || !Number.isFinite(latitude)
    || Math.abs(longitude) > 180 || Math.abs(latitude) > 90) return null;
  return [longitude, latitude];
}

/** Before publication only selected task routes are available. */
export function routeMap(routes: ConfirmedRoute[]): MapView {
  const places = new Map<string, Place>();
  for (const { origin, destination } of routes) {
    for (const place of [origin, destination]) {
      if (!places.has(place.poi_id)) places.set(place.poi_id, place);
    }
  }
  const entries = routes.map((route) => ({ route }));
  return {
    status: routes.length ? "complete" : "unavailable",
    stops: numberedStops([...places.values()].map((poi) => ({ poi }))),
    days: [], issues: [], routes: entries,
    legs: entries.map((entry) => ({ selected: entry, alternatives: [entry] })),
  };
}

function numberedStops(places: NonNullable<MapData["places"]>, order: string[] = []) {
  const index = new Map(places.map((place) => [place.poi.poi_id, place]));
  const ids = new Set([...order, ...index.keys()]);
  const stops: MapStop[] = [];
  for (const id of ids) {
    const entry = index.get(id);
    if (!entry) continue;
    const position = mapPoint(entry.poi.coordinates);
    if (position) stops.push({ place: entry.poi, number: stops.length + 1, position, category: entry.category });
  }
  return stops;
}

export function reportMap(data: MapData): MapView {
  const routes = data.routes ?? [];
  const days = data.days ?? [];
  const byRouteId = new Map(routes.map((entry) => [entry.route.route_id, entry]));
  const stops = numberedStops(data.places ?? [], days.flatMap((day) => day.stops.map(({ poi_id }) => poi_id)));
  const byPoiId = new Map(stops.map((stop) => [stop.place.poi_id, stop]));
  const mappedDays = days.map((day): MapDayView => ({
    number: day.day_number,
    date: day.on_date,
    schedule: new Map(day.stops.map((stop) => [stop.poi_id, stop])),
    stops: day.stops.map(({ poi_id }) => byPoiId.get(poi_id)).filter((stop) => stop !== undefined),
    legs: (day.legs ?? []).flatMap((leg) => {
      const selected = byRouteId.get(leg.selected_route_id);
      if (!selected) return [];
      const alternatives = leg.alternative_route_ids.map((id) => byRouteId.get(id)).filter((entry) => entry !== undefined);
      return [{ selected, alternatives }];
    }),
  }));
  return { status: data.status, stops, days: mappedDays, legs: mappedDays.flatMap((day) => day.legs), routes, issues: data.issues ?? [] };
}

export function dayForPoi(view: MapView, current: number, poiId: string): number {
  if (current === 0 || view.days.find((day) => day.number === current)?.schedule.has(poiId)) return current;
  return view.days.find((day) => day.schedule.has(poiId))?.number ?? 0;
}

export function legOptions(leg: RouteLeg, mode: RouteMode | null) {
  if (mode === null) return [leg.selected];
  return leg.alternatives.filter(({ route }) => route.mode === mode);
}

export function routeModes(legs: RouteLeg[]): RouteMode[] {
  return [...new Set(legs.flatMap((leg) => leg.alternatives.map(({ route }) => route.mode)))];
}

export function routePaths(routes: MapRoute[]) {
  return routes.flatMap(({ route, segments }) => {
    const geometry = segments?.length ? segments : [{ path: route.polyline, mode: route.mode }];
    return geometry.flatMap(({ path, mode }) => {
      const points = path?.map(mapPoint);
      return points && points.length > 1 && points.every((point) => point !== null)
        ? [{ routeId: route.route_id, mode, points }]
        : [];
    });
  });
}

export function splitPoiMentions(text: string, stops: readonly PoiName[]) {
  const names = new Map<string, string | null>();
  for (const { name, id } of stops) {
    if (name) names.set(name, !names.has(name) || names.get(name) === id ? id : null);
  }
  const alternatives = [...names.keys()].sort((left, right) => right.length - left.length);
  if (!alternatives.length) return [{ text, id: null }];
  const pattern = new RegExp(alternatives.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
  const parts: { text: string; id: string | null }[] = [];
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    const [name] = match;
    if (position < match.index) parts.push({ text: text.slice(position, match.index), id: null });
    parts.push({ text: name, id: names.get(name) ?? null });
    position = match.index + name.length;
  }
  if (position < text.length) parts.push({ text: text.slice(position), id: null });
  return parts;
}

export function commuteLabel(distance: number, seconds: number) {
  return `${distanceLabel(distance)} · 预计 ${durationLabel(seconds)}`;
}

export function distanceLabel(distance: number) {
  return distance < 1000 ? `${distance} 米` : `${Number((distance / 1000).toFixed(2))} 公里`;
}

export function durationLabel(seconds: number) {
  const minutes = Math.round(seconds / 60);
  return minutes > 0 ? `${minutes} 分钟` : `${seconds} 秒`;
}
