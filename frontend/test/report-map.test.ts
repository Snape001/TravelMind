import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { routeMap, routePaths, splitPoiMentions, type ConfirmedRoute } from "../src/lib/report-map";
import { readTaskRoutes } from "../src/lib/task-client";

const { route } = JSON.parse(readFileSync("../fixtures/phase4.json", "utf8")) as { route: ConfirmedRoute };

test("same confirmed POI keeps its number across transport modes and missing geometry", () => {
  const walking = { ...route, mode: "walking" as const };
  const transit = { ...route, mode: "transit" as const, polyline: null, route_id: "other" };
  const plan = routeMap([walking, transit]);
  assert.deepEqual(plan.modes, ["walking", "transit"]);
  assert.deepEqual(plan.stops.map(({ place, number }) => [place.poi_id, number]),
    [[walking.origin.poi_id, 1], [walking.destination.poi_id, 2]]);
  assert.deepEqual(routePaths([transit]), []);
  assert.ok(plan.routes.some(({ distance_m, duration_seconds }) =>
    distance_m === transit.distance_m && duration_seconds === transit.duration_seconds));
});

test("route geometry keeps independent polylines and rejects partial invalid paths", () => {
  const left = { ...route, polyline: [{ crs: "gcj02" as const, longitude: 116, latitude: 22 },
    { crs: "gcj02" as const, longitude: 116.1, latitude: 22 }] };
  const right = { ...route, route_id: "next", polyline: [{ crs: "gcj02" as const, longitude: 118, latitude: 24 },
    { crs: "gcj02" as const, longitude: 118.1, latitude: 24 }] };
  assert.deepEqual(routePaths([left, right]).map(({ points }) => points), [
    [[116, 22], [116.1, 22]], [[118, 24], [118.1, 24]],
  ]);
  assert.deepEqual(routePaths([{ ...left, polyline: [...left.polyline, { crs: "gcj02", longitude: 999, latitude: 22 }] }]), []);
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

test("routes are loaded only from the frozen same-origin task endpoint", async (context) => {
  context.mock.method(globalThis, "fetch", async (path: string, request: RequestInit) => {
    assert.equal(path, "/api/v1/tasks/my%20task/routes");
    assert.equal(request.cache, "no-store");
    return Response.json({ task_id: "my task", routes: [route] });
  });
  assert.deepEqual((await readTaskRoutes("my task", new AbortController().signal)).routes, [route]);
});
