import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  dayForPoi, legOptions, reportMap, routeMap, routeModes, routePaths, splitPoiMentions,
  type ConfirmedRoute, type MapData,
} from "../src/lib/report-map";
import { readReportMap, readTaskRoutes } from "../src/lib/task-client";

const { route } = JSON.parse(readFileSync("../fixtures/phase4.json", "utf8")) as { route: ConfirmedRoute };

test("same confirmed POI keeps its number across transport modes and missing geometry", () => {
  const walking = { ...route, mode: "walking" as const };
  const transit = { ...route, mode: "transit" as const, polyline: null, route_id: "other" };
  const plan = routeMap([walking, transit]);
  assert.deepEqual(routeModes(plan.legs), ["walking", "transit"]);
  assert.deepEqual(plan.stops.map(({ place, number }) => [place.poi_id, number]),
    [[walking.origin.poi_id, 1], [walking.destination.poi_id, 2]]);
  assert.deepEqual(routePaths([{ route: transit }]), []);
  assert.ok(plan.routes.some(({ route: { distance_m, duration_seconds } }) =>
    distance_m === transit.distance_m && duration_seconds === transit.duration_seconds));
});

test("route geometry keeps independent polylines and rejects partial invalid paths", () => {
  const left = { ...route, polyline: [{ crs: "gcj02" as const, longitude: 116, latitude: 22 },
    { crs: "gcj02" as const, longitude: 116.1, latitude: 22 }] };
  const right = { ...route, route_id: "next", polyline: [{ crs: "gcj02" as const, longitude: 118, latitude: 24 },
    { crs: "gcj02" as const, longitude: 118.1, latitude: 24 }] };
  assert.deepEqual(routePaths([{ route: left }, { route: right }]).map(({ points }) => points), [
    [[116, 22], [116.1, 22]], [[118, 24], [118.1, 24]],
  ]);
  assert.deepEqual(routePaths([{ route: { ...left, polyline: [...left.polyline, { crs: "gcj02", longitude: 999, latitude: 22 }] } }]), []);
});

test("report links use the longest unambiguous exact POI without rewriting report text", () => {
  const stops = [{ id: "quanzhou", name: "泉州开元寺" }, { id: "b1", name: "开元寺" },
    { id: "b2", name: "开元寺" }, { id: "zhonglou", name: "钟楼" }];
  const body = "从泉州开元寺到钟楼。\n开元寺可能有同名地点。";
  const parts = splitPoiMentions(body, stops);
  assert.deepEqual(parts.filter(({ id }) => id).map(({ id, text }) => [id, text]), [
    ["quanzhou", "泉州开元寺"], ["zhonglou", "钟楼"],
  ]);
  assert.equal(parts.map(({ text }) => text).join(""), body);
});

test("map reads use the owning task and exact report version without caching", async (context) => {
  const selected = { task_id: "my task", routes: [route] };
  const unavailable = { task_id: "my task", report_id: "x/y", status: "unavailable" };
  const requests: string[] = [];
  context.mock.method(globalThis, "fetch", async (path: string, request: RequestInit) => {
    assert.equal(request.cache, "no-store");
    requests.push(path);
    return Response.json(path.endsWith("/routes") ? selected : unavailable);
  });
  const { signal } = new AbortController();
  assert.deepEqual(await readTaskRoutes("my task", signal), selected);
  assert.deepEqual(await readReportMap("my task", "x/y", signal), unavailable);
  assert.deepEqual(requests, [
    "/api/v1/tasks/my%20task/routes", "/api/v1/tasks/my%20task/reports/x%2Fy/map-data",
  ]);
});

test("report MapData keeps day boundaries, stop times and ranked alternatives", () => {
  const later = { ...route.destination, poi_id: "second-day", name: "海边展馆" };
  const fasterBus = { ...route, mode: "transit" as const, route_id: "bus-fast" };
  const slowerBus = { ...fasterBus, route_id: "bus-slow" };
  const data: MapData = {
    task_id: "task", report_id: "report", status: "partial",
    places: [{ poi: later }, { poi: route.destination }, { poi: route.origin }],
    routes: [{ route }, { route: fasterBus }, { route: slowerBus }],
    days: [
      { day_number: 1, stops: [{ poi_id: route.origin.poi_id, suggested_minutes: 65, suggested_start: "09:00:00" },
        { poi_id: route.destination.poi_id, suggested_minutes: 90 }],
        legs: [{ selected_route_id: route.route_id, alternative_route_ids: [route.route_id, "bus-fast", "bus-slow"] }] },
      { day_number: 2, on_date: "2026-12-01", stops: [{ poi_id: later.poi_id, suggested_minutes: 55 }] },
    ],
    issues: [{ code: "GEOMETRY", message: "存在缺口" }],
  };
  const view = reportMap(data);
  assert.deepEqual(view.stops.map(({ place, number }) => [place.poi_id, number]),
    [[route.origin.poi_id, 1], [route.destination.poi_id, 2], [later.poi_id, 3]]);
  assert.equal(view.days[0].schedule.get(route.origin.poi_id)?.suggested_minutes, 65);
  assert.equal(view.days[1].schedule.get(later.poi_id)?.suggested_minutes, 55);
  assert.equal(dayForPoi(view, 1, later.poi_id), 2);
  assert.equal(dayForPoi(view, 0, later.poi_id), 0);
  assert.equal(view.issues[0].message, "存在缺口");
  const [leg] = view.days[0].legs;
  assert.deepEqual(legOptions(leg, null).map(({ route }) => route.route_id), [route.route_id]);
  assert.deepEqual(legOptions(leg, "transit").map(({ route }) => route.route_id), ["bus-fast", "bus-slow"]);
  assert.deepEqual(legOptions(leg, "cycling"), []);
});

test("disjoint bus and walking segments remain independent even when an aggregate polyline exists", () => {
  const left = route.origin.coordinates;
  const right = route.destination.coordinates;
  const far = { ...right, longitude: right.longitude + 0.1 };
  const aggregated = { ...route, mode: "transit" as const, polyline: [left, far] };
  const geometry = routePaths([{ route: aggregated, segments: [
    { mode: "walking", path: [left, right] },
    { mode: "bus", name: "1路", path: null },
    { mode: "railway", path: [far, { ...far, latitude: far.latitude + 0.1 }] },
  ] }]);
  assert.deepEqual(geometry.map(({ mode, points }) => [mode, points]), [
    ["walking", [[left.longitude, left.latitude], [right.longitude, right.latitude]]],
    ["railway", [[far.longitude, far.latitude], [far.longitude, far.latitude + 0.1]]],
  ]);
  assert.deepEqual(routePaths([{ route: aggregated, segments: [{ mode: "bus", path: null }] }]), []);
});
