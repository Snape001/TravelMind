"use client";

import { useEffect, useRef, useState } from "react";
import { loadAmapSdk, type AmapMap, type AmapSdk } from "@/lib/amap";
import type { MapStop, routePaths } from "@/lib/report-map";

type MapState = { kind: "loading" } | { kind: "error" } | { kind: "ready"; api: AmapSdk; map: AmapMap };
export type PoiSelection = { id: string };

export function RouteMapCanvas({ stops, paths, selection, onSelect }: {
  stops: MapStop[];
  paths: ReturnType<typeof routePaths>;
  selection: PoiSelection | null;
  onSelect: (id: string) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const activeMap = useRef<AmapMap | undefined>(undefined);
  const markers = useRef(new Map<string, { element: HTMLButtonElement; overlay: object }>());
  const [center] = useState(stops[0].position);
  const [state, setState] = useState<MapState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const container = target.current;
    let active = true;
    let map: AmapMap | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let initialized: (() => void) | undefined;

    function fail() {
      if (!active) return;
      if (initialized) map?.off("complete", initialized);
      map?.destroy();
      map = undefined;
      setState({ kind: "error" });
    }

    void loadAmapSdk().then((api) => {
      if (!active || !container) return;
      const instance = new api.Map(container, { viewMode: "2D", center, zoom: 13 });
      map = instance;
      activeMap.current = instance;
      const complete = () => {
        if (!active) return;
        clearTimeout(timer);
        instance.off("complete", complete);
        setState({ kind: "ready", api, map: instance });
      };
      initialized = complete;
      instance.on("complete", complete);
      timer = setTimeout(fail, 12000);
    }).catch(fail);
    return () => {
      active = false;
      clearTimeout(timer);
      if (initialized) map?.off("complete", initialized);
      activeMap.current = undefined;
      map?.destroy();
    };
  }, [attempt, center]);

  useEffect(() => {
    if (state.kind !== "ready") return;
    const { api, map } = state;
    const current = new Map<string, { element: HTMLButtonElement; overlay: object }>();
    for (const { place, position, number } of stops) {
      const element = document.createElement("button");
      element.type = "button";
      element.className = "map-poi-marker";
      element.textContent = String(number);
      element.setAttribute("aria-label", `地图景点 ${number} ${place.name}`);
      element.onclick = () => onSelect(place.poi_id);
      const overlay = new api.Marker({ content: element, position, anchor: "center", title: place.name });
      current.set(place.poi_id, { element, overlay });
    }
    markers.current = current;
    const overlays = [...current.values()].map(({ overlay }) => overlay);
    map.add(overlays);
    return () => {
      markers.current = new Map();
      for (const { element } of current.values()) element.onclick = null;
      if (activeMap.current === map) map.remove(overlays);
    };
  }, [state, stops, onSelect]);

  useEffect(() => {
    if (state.kind !== "ready") return;
    const { api, map } = state;
    const polylines = paths.map(({ points }) => new api.Polyline({
      path: points, strokeColor: "#21645d", strokeWeight: 6, isOutline: true,
      borderWeight: 1, outlineColor: "#fffdf5", lineJoin: "round", lineCap: "round",
    }));
    if (polylines.length) map.add(polylines);
    const overlays = [...polylines, ...[...markers.current.values()].map(({ overlay }) => overlay)];
    map.setFitView(overlays, true, [38, 38, 38, 38], 16);
    return () => { if (activeMap.current === map && polylines.length) map.remove(polylines); };
  }, [state, paths, stops, onSelect]);

  useEffect(() => {
    if (state.kind !== "ready") return;
    for (const [id, { element }] of markers.current) {
      element.setAttribute("aria-pressed", String(id === selection?.id));
    }
    const stop = stops.find(({ place }) => place.poi_id === selection?.id);
    if (stop) state.map.setZoomAndCenter(16, stop.position);
  }, [selection, state, stops, onSelect]);

  return <div className="map-canvas-wrap" data-map-state={state.kind}>
    <div ref={target} className="map-canvas" role="group" aria-label="目的地路线地图" />
    {state.kind === "loading" && <div className="map-overlay" role="status">正在加载高德地图…</div>}
    {state.kind === "error" && <div className="map-overlay">
      <p role="status">地图暂不可用，可继续查看右侧景点和通勤数据。</p>
      <button className="button secondary" type="button" onClick={() => {
        setState({ kind: "loading" }); setAttempt((value) => value + 1);
      }}>重试地图</button>
    </div>}
  </div>;
}
