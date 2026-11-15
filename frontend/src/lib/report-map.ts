import type { components } from "./phase4.generated";

export type ConfirmedRoute = components["schemas"]["TravelRoute"];
export type RouteMode = ConfirmedRoute["mode"];
type Place = ConfirmedRoute["origin"];
export type MapPoint = [longitude: number, latitude: number];
export type MapStop = { place: Place; number: number; position: MapPoint };
export type PoiName = { id: string; name: string };

export const modeLabel: Record<RouteMode, string> = {
  driving: "驾车", transit: "公交", walking: "步行", cycling: "骑行",
};

function mapPoint(point: { crs?: string; longitude: number; latitude: number }): MapPoint | null {
  const { longitude, latitude } = point;
  if ((point.crs && point.crs !== "gcj02") || !Number.isFinite(longitude) || !Number.isFinite(latitude)
    || Math.abs(longitude) > 180 || Math.abs(latitude) > 90) return null;
  return [longitude, latitude];
}

export function routeMap(routes: ConfirmedRoute[]) {
  const stops = new Map<string, MapStop>();
  const modes = new Set<RouteMode>();
  for (const route of routes) {
    modes.add(route.mode);
    for (const place of [route.origin, route.destination]) {
      const position = mapPoint(place.coordinates);
      if (position && !stops.has(place.poi_id)) {
        stops.set(place.poi_id, { place, number: stops.size + 1, position });
      }
    }
  }
  return { routes, stops: [...stops.values()], modes: [...modes] };
}

export function routePaths(routes: ConfirmedRoute[]) {
  return routes.flatMap((route) => {
    const points = route.polyline?.map(mapPoint);
    return points && points.length > 1 && points.every((point) => point !== null)
      ? [{ id: route.route_id, points }]
      : [];
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
  const length = distance < 1000 ? `${distance} 米` : `${Number((distance / 1000).toFixed(2))} 公里`;
  const minutes = Math.round(seconds / 60);
  const time = minutes > 0 ? `${minutes} 分钟` : `${seconds} 秒`;
  return `${length} · 预计 ${time}`;
}
