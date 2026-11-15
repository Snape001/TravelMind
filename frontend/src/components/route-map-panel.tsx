"use client";

import { MapPin, Route } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { commuteLabel, modeLabel, routeMap, routePaths, type RouteMode } from "@/lib/report-map";
import { missingEndpoint, readTaskRoutes } from "@/lib/task-client";
import { RouteMapCanvas, type PoiSelection } from "./route-map-canvas";

type RouteData = ReturnType<typeof routeMap>;
type LoadState = { kind: "loading" | "empty" | "error" } | { kind: "ready"; data: RouteData };
export type ReportChapter = { id: string; title: string };

export function useTaskRouteMap(taskId: string, enabled: boolean) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      setState({ kind: "error" });
    }, 10000);

    void readTaskRoutes(taskId, controller.signal).then((payload) => {
      if (controller.signal.aborted) return;
      if (payload.task_id !== taskId || !Array.isArray(payload.routes)) throw new Error("invalid route response");
      setState(payload.routes.length ? { kind: "ready", data: routeMap(payload.routes) } : { kind: "empty" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ kind: missingEndpoint(error) ? "empty" : "error" });
    }).finally(() => clearTimeout(timer));
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [attempt, enabled, taskId]);

  return {
    state: enabled ? state : { kind: "empty" } as const,
    retry() { setState({ kind: "loading" }); setAttempt((value) => value + 1); },
  };
}

export function RouteMapPanel({ state, selection, onSelect, chapters, retry }: {
  state: LoadState;
  selection: PoiSelection | null;
  onSelect: (id: string) => void;
  chapters?: ReadonlyMap<string, ReportChapter[]>;
  retry: () => void;
}) {
  if (state.kind === "ready") {
    return <RouteMapContent data={state.data} selection={selection} onSelect={onSelect} chapters={chapters} />;
  }
  return <div className="map-placeholder">
    <Route size={20} aria-hidden />
    <p role="status">{state.kind === "loading" ? "正在读取路线数据…" : state.kind === "error"
      ? "暂时无法读取路线数据。" : "此任务暂无可展示的地图与路线数据。"}</p>
    {state.kind === "error" && <button className="button secondary" type="button" onClick={retry}>重试读取路线</button>}
  </div>;
}

function RouteMapContent({ data, selection, onSelect, chapters }: {
  data: RouteData;
  selection: PoiSelection | null;
  onSelect: (id: string) => void;
  chapters?: ReadonlyMap<string, ReportChapter[]>;
}) {
  const [mode, setMode] = useState<RouteMode>(data.modes[0]);
  const routes = useMemo(() => data.routes.filter((route) => route.mode === mode), [data, mode]);
  const paths = useMemo(() => routePaths(routes), [routes]);
  const withGeometry = new Set(paths.map(({ id }) => id));
  const selected = data.stops.find(({ place }) => place.poi_id === selection?.id);
  const references = selected && chapters?.get(selected.place.poi_id);

  return <>
    <div className="map-mode-row" role="group" aria-label="交通方式">
      {data.modes.map((item) => <button key={item} type="button" className="map-mode"
        aria-pressed={mode === item} onClick={() => setMode(item)}>{modeLabel[item]}</button>)}
    </div>
    <div className="route-map-grid">
      {data.stops.length > 0
        ? <RouteMapCanvas stops={data.stops} paths={paths} selection={selection} onSelect={onSelect} />
        : <div className="map-placeholder"><p>已返回通勤数据，暂无可绘制的地点坐标。</p></div>}
      <div className="map-detail-panel">
        <section aria-label="景点信息">
          <h3>景点</h3>
          <ul className="map-stop-list">
            {data.stops.map(({ place, number }) => <li key={place.poi_id}>
              <button type="button" aria-pressed={selection?.id === place.poi_id} onClick={() => onSelect(place.poi_id)}>
                <span className="map-stop-number" aria-hidden>{number}</span>{place.name}
              </button>
            </li>)}
          </ul>
          {selected && <div className="map-stop-detail" aria-live="polite">
            <h4><MapPin size={15} aria-hidden />{selected.place.name}</h4>
            <p>{[...new Set([
              selected.place.administrative.province, selected.place.administrative.city,
              selected.place.administrative.district, selected.place.address,
            ].filter(Boolean))].join(" · ") || "地址暂未提供"}</p>
            <p>建议停留时间暂未提供</p>
            {!!references?.length && <ul aria-label="相关报告章节">{references.map(({ id, title }) =>
              <li key={id}><a className="text-link" href={`#${id}`}>{title}</a></li>)}</ul>}
          </div>}
        </section>
        <section aria-label="通勤路线">
          <h3>通勤路线</h3>
          <ul className="map-legs">{routes.map((route) => <li key={route.route_id}>
            <strong>{route.origin.name} → {route.destination.name}</strong>
            <span>{commuteLabel(route.distance_m, route.duration_seconds)}</span>
            {!withGeometry.has(route.route_id) && <small>暂无可绘制的路线几何</small>}
          </li>)}</ul>
        </section>
      </div>
    </div>
  </>;
}
