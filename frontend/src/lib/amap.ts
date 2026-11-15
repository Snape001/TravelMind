import type { MapPoint } from "./report-map";

export interface AmapMap {
  on(event: "complete", listener: () => void): void;
  off(event: "complete", listener: () => void): void;
  add(overlays: object[]): void;
  remove(overlays: object[]): void;
  setFitView(overlays: object[], immediately: boolean, padding: number[], maxZoom: number): void;
  setZoomAndCenter(zoom: number, center: MapPoint): void;
  destroy(): void;
}

export interface AmapSdk {
  Map: new (target: HTMLElement, options: { viewMode: "2D"; center: MapPoint; zoom: number }) => AmapMap;
  Marker: new (options: { content: HTMLElement; position: MapPoint; anchor: "center"; title: string }) => object;
  Polyline: new (options: {
    path: MapPoint[]; strokeColor: string; strokeWeight: number; borderWeight: number; outlineColor: string;
    isOutline: boolean; lineJoin: "round"; lineCap: "round";
  }) => object;
}

type MapWindow = Window & {
  AMap?: AmapSdk;
  _AMapSecurityConfig?: { serviceHost: string };
};

let sdk: Promise<AmapSdk> | undefined;

/** Load the map SDK on demand from a signed-in map view. */
export function loadAmapSdk(): Promise<AmapSdk> {
  sdk ??= initialize().catch((error: unknown) => {
    sdk = undefined;
    throw error;
  });
  return sdk;
}

async function initialize(): Promise<AmapSdk> {
  const browser = window as MapWindow;
  const response = await fetch("/_AMapService/config", {
    cache: "no-store", signal: AbortSignal.timeout(8000),
  });
  if (response.status === 503) throw new Error("高德地图 JS API 凭据未配置");
  if (!response.ok) throw new Error("无法读取高德地图配置");
  const config: { js_api_key?: unknown } = await response.json();
  if (typeof config.js_api_key !== "string" || !config.js_api_key) {
    throw new Error("未配置高德地图 JS API Key");
  }
  browser._AMapSecurityConfig = { serviceHost: `${window.location.origin}/_AMapService` };
  const url = new URL("https://webapi.amap.com/maps");
  url.searchParams.set("v", "2.0");
  url.searchParams.set("key", config.js_api_key);
  return new Promise<AmapSdk>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url.href;
    script.async = true;
    script.referrerPolicy = "origin";
    const failed = () => {
      clearTimeout(timer);
      script.remove();
      reject(new Error("高德地图 SDK 加载失败"));
    };
    const timer = window.setTimeout(failed, 12000);
    script.onload = () => {
      const map = browser.AMap;
      if (!map?.Map || !map.Marker || !map.Polyline) {
        failed();
        return;
      }
      clearTimeout(timer);
      resolve(map);
    };
    script.onerror = failed;
    document.head.append(script);
  });
}
