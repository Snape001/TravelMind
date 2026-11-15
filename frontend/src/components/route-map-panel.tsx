"use client";

import { MapPin, Route } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  commuteLabel, dayForPoi, distanceLabel, durationLabel, legOptions, modeLabel, reportMap,
  routeMap, routeModes, routePaths, segmentLabel,
  type MapRoute, type MapStop, type MapView, type RouteMode,
} from "@/lib/report-map";
import { missingEndpoint, readReportMap, readTaskRoutes } from "@/lib/task-client";
import { RouteMapCanvas, type PoiSelection } from "./route-map-canvas";

type LoadState = { kind: "loading" | "empty" | "error" } | { kind: "ready"; data: MapView };
export type ReportChapter = { id: string; title: string };

export function useRouteMap(taskId: string, reportId?: string, enabled = true) {
  const source = `${taskId}:${reportId ?? ""}`;
  const [result, setResult] = useState<{ source: string; state: LoadState }>();
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const update = (state: LoadState) => setResult({ source, state });
    const timer = setTimeout(() => {
      controller.abort();
      update({ kind: "error" });
    }, 10000);

    const load = reportId
      ? readReportMap(taskId, reportId, controller.signal).then((payload) => {
        if (payload.task_id !== taskId || payload.report_id !== reportId
          || !["complete", "partial", "unavailable"].includes(payload.status)) {
          throw new Error("地图快照与当前报告不匹配");
        }
        return reportMap(payload);
      })
      : readTaskRoutes(taskId, controller.signal).then((payload) => {
        if (payload.task_id !== taskId || !Array.isArray(payload.routes)) throw new Error("路线数据与当前任务不匹配");
        return routeMap(payload.routes);
      });

    void load.then((data) => {
      if (!controller.signal.aborted) update(data.status === "unavailable" ? { kind: "empty" } : { kind: "ready", data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) update({ kind: missingEndpoint(error) ? "empty" : "error" });
    }).finally(() => clearTimeout(timer));
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [attempt, enabled, source, taskId, reportId]);

  const state: LoadState = !enabled ? { kind: "empty" }
    : result?.source === source ? result.state : { kind: "loading" };
  return {
    state,
    retry() { setResult({ source, state: { kind: "loading" } }); setAttempt((value) => value + 1); },
  };
}

type PanelLinks = {
  chapters?: ReadonlyMap<string, ReportChapter[]>;
  evidenceLinks?: ReadonlyMap<string, number>;
};

export function RouteMapPanel({ state, selection, onSelect, chapters, evidenceLinks, retry }: PanelLinks & {
  state: LoadState;
  selection: PoiSelection | null;
  onSelect: (id: string) => void;
  retry: () => void;
}) {
  if (state.kind === "ready") {
    return <RouteMapContent data={state.data} selection={selection} onSelect={onSelect}
      chapters={chapters} evidenceLinks={evidenceLinks} />;
  }
  return <div className="map-placeholder">
    <Route size={20} aria-hidden />
    <p role="status">{state.kind === "loading" ? "正在读取路线数据…" : state.kind === "error"
      ? "暂时无法读取路线数据。" : "暂无可展示的地图与路线数据。"}</p>
    {state.kind === "error" && <button className="button secondary" type="button" onClick={retry}>重试读取路线</button>}
  </div>;
}

function RouteMapContent({ data, selection, onSelect, chapters, evidenceLinks }: PanelLinks & {
  data: MapView;
  selection: PoiSelection | null;
  onSelect: (id: string) => void;
}) {
  const [dayChoice, setDayChoice] = useState({ number: data.days[0]?.number ?? 0, since: null as PoiSelection | null });
  const [mode, setMode] = useState<RouteMode | null>(null);
  const [alternatives, setAlternatives] = useState<Record<string, string>>({});
  const dayNumber = selection && selection !== dayChoice.since
    ? dayForPoi(data, dayChoice.number, selection.id) : dayChoice.number;
  const day = data.days.find(({ number }) => number === dayNumber);
  const stops = day?.stops ?? data.stops;
  const { dayModes, activeMode, rows, paths } = useMemo(() => {
    const legs = data.days.find(({ number }) => number === dayNumber)?.legs ?? data.legs;
    const dayModes = routeModes(legs);
    const activeMode = mode && dayModes.includes(mode) ? mode : null;
    const shown = new Map<string, MapRoute>();
    const rows = legs.map((leg) => {
      const options = legOptions(leg, activeMode);
      const id = alternatives[leg.selected.route.route_id];
      const current = options.find(({ route }) => route.route_id === id)
        ?? options.find(({ route }) => route.route_id === leg.selected.route.route_id) ?? options[0];
      if (current) shown.set(current.route.route_id, current);
      return { leg, options, current };
    });
    return { dayModes, activeMode, rows, paths: routePaths([...shown.values()]) };
  }, [data, dayNumber, mode, alternatives]);
  const withGeometry = new Set(paths.map(({ routeId }) => routeId));
  const selected = stops.find(({ place }) => place.poi_id === selection?.id);

  return <>
    {data.status === "partial" && <div className="map-partial">
      <p role="status">地图数据不完整，部分景点或路段仍待核验。</p>
      {data.issues.length > 0 && <details>
        <summary>查看数据缺口</summary>
        <ul>{[...new Set(data.issues.map(({ message }) => message))].map((message) => <li key={message}>{message}</li>)}</ul>
      </details>}
    </div>}
    {data.days.length > 0 && <div className="map-mode-row map-day-row" role="group" aria-label="行程日期">
      {data.days.map(({ number, date }) => <button key={number} type="button" className="map-mode"
        aria-pressed={dayNumber === number} onClick={() => setDayChoice({ number, since: selection })}>
        第 {number} 天{date && <small>{date}</small>}
      </button>)}
      <button type="button" className="map-mode" aria-pressed={dayNumber === 0}
        onClick={() => setDayChoice({ number: 0, since: selection })}>全部景点</button>
    </div>}
    {dayModes.length > 0 && <div className="map-mode-row" role="group" aria-label="交通方式">
      <button type="button" className="map-mode" aria-pressed={activeMode === null} onClick={() => setMode(null)}>按计划</button>
      {dayModes.map((item) => <button key={item} type="button" className="map-mode"
        aria-pressed={activeMode === item} onClick={() => setMode(item)}>{modeLabel[item]}</button>)}
    </div>}
    <div className="route-map-grid">
      {stops.length > 0
        ? <RouteMapCanvas stops={stops} paths={paths} selection={selection} onSelect={onSelect} />
        : <div className="map-placeholder"><p>暂无可绘制的地点坐标。</p></div>}
      <div className="map-detail-panel">
        {stops.length > 0 && <section aria-label="景点信息">
          <h3>景点</h3>
          <ul className="map-stop-list">
            {stops.map(({ place, number }, index) => {
              const scheduled = day?.schedule.get(place.poi_id);
              return <li key={`${place.poi_id}:${index}`}>
                <button type="button" aria-pressed={selection?.id === place.poi_id} onClick={() => onSelect(place.poi_id)}>
                  <span className="map-stop-number" aria-hidden>{number}</span>
                  <span>{place.name}{scheduled && <small>建议停留 {scheduled.suggested_minutes} 分钟</small>}</span>
                </button>
              </li>;
            })}
          </ul>
          {selected && <StopDetail stop={selected} days={day ? [day] : data.days}
            references={chapters?.get(selected.place.poi_id)} />}
        </section>}
        <section aria-label="通勤路线">
          <h3>通勤路线</h3>
          {rows.length === 0 ? <p className="muted">当前行程暂无通勤路段。</p>
            : <ul className="map-legs">{rows.map(({ leg, options, current }, index) => {
              const base = leg.selected.route;
              const title = `${base.origin.name} → ${base.destination.name}`;
              return <li key={`${base.route_id}:${index}`}>
                <strong>{title}</strong>
                {options.length > 1 && <select aria-label={`${title}的备选路线`} value={current?.route.route_id}
                  onChange={(event) => {
                    const id = event.target.value;
                    setAlternatives((previous) => ({ ...previous, [base.route_id]: id }));
                  }}>
                  {options.map(({ route }) => <option key={route.route_id} value={route.route_id}>
                    {route.route_id === base.route_id ? "已选" : "备选"} · {commuteLabel(route.distance_m, route.duration_seconds)}
                  </option>)}
                </select>}
                {current
                  ? <Commute entry={current} selected={current.route.route_id === base.route_id}
                    hasGeometry={withGeometry.has(current.route.route_id)} evidenceLinks={evidenceLinks} />
                  : <small>此路段暂无所选交通方式的方案。</small>}
              </li>;
            })}</ul>}
        </section>
      </div>
    </div>
  </>;
}

function StopDetail({ stop, days, references }: {
  stop: MapStop; days: MapView["days"]; references?: ReportChapter[];
}) {
  const { place } = stop;
  const { province, city, district } = place.administrative;
  const location = [...new Set([province, city, district, place.address].filter(Boolean))].join(" · ");
  const visits = days.flatMap((day) => {
    const entry = day.schedule.get(place.poi_id);
    return entry ? [{ day: day.number, ...entry }] : [];
  });
  return <div className="map-stop-detail" aria-live="polite">
    <h4><MapPin size={15} aria-hidden />{place.name}</h4>
    {stop.category && <p>{stop.category}</p>}
    <p>{location || "地址暂未提供"}</p>
    {visits.map((visit) => <p key={visit.day}>
      第 {visit.day} 天{visit.suggested_start ? ` · 建议 ${visit.suggested_start.slice(0, 5)} 到访` : ""}
      {" · "}建议停留 {visit.suggested_minutes} 分钟
    </p>)}
    {!!references?.length && <ul aria-label="相关报告章节">{references.map(({ id, title }) =>
      <li key={id}><a className="text-link" href={`#${id}`}>{title}</a></li>)}</ul>}
  </div>;
}

const queriedAt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function Commute({ entry, selected, hasGeometry, evidenceLinks }: {
  entry: MapRoute; selected: boolean; hasGeometry: boolean; evidenceLinks?: ReadonlyMap<string, number>;
}) {
  const { route, segments = [], evidence_id } = entry;
  const evidence = evidence_id ? evidenceLinks?.get(evidence_id) : undefined;
  const fetched = new Date(route.fetched_at);
  return <>
    <span>{modeLabel[route.mode]} · {selected ? "已选路线" : "备选路线"}</span>
    <span>{commuteLabel(route.distance_m, route.duration_seconds)}</span>
    <small>高德地图{!Number.isNaN(fetched.getTime()) && ` · 查询于 ${queriedAt.format(fetched)}`}
      {evidence && <a className="text-link map-evidence-link" href={`#evidence-${evidence_id}`}>路线证据 [{evidence}]</a>}
    </small>
    {!hasGeometry && <small>暂无可绘制的路线几何。</small>}
    {hasGeometry && segments.some((segment) => !segment.path?.length) && <small>部分分段暂无几何。</small>}
    {route.mode === "transit" && segments.length > 0 && <details className="map-transit">
      <summary>查看公交换乘与分段</summary>
      <ol>{segments.map((item, index) => <li key={index}>
        <strong>{item.name || segmentLabel[item.mode]}</strong>
        {(item.departure_stop || item.arrival_stop) && <span>
          {item.departure_stop?.name && `上车 ${item.departure_stop.name}`}
          {item.departure_stop && item.arrival_stop && " · "}
          {item.arrival_stop?.name && `下车 ${item.arrival_stop.name}`}
        </span>}
        {(item.distance_m != null || item.duration_seconds != null) && <small>{[
          item.distance_m != null && distanceLabel(item.distance_m),
          item.duration_seconds != null && `预计 ${durationLabel(item.duration_seconds)}`,
        ].filter(Boolean).join(" · ")}</small>}
      </li>)}</ol>
    </details>}
  </>;
}
