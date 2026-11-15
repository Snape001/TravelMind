type MapWindow = Window & {
  AMap?: unknown;
  _AMapSecurityConfig?: { serviceHost: string };
};

let sdk: Promise<void> | undefined;

/** Load the map SDK on demand from a signed-in map view. */
export function loadAmapSdk(): Promise<void> {
  sdk ??= initialize().catch((error: unknown) => {
    sdk = undefined;
    throw error;
  });
  return sdk;
}

async function initialize(): Promise<void> {
  const browser = window as MapWindow;
  const response = await fetch("/_AMapService/config", { cache: "no-store" });
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
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url.href;
    script.async = true;
    script.referrerPolicy = "origin";
    const failed = () => {
      script.remove();
      reject(new Error("高德地图 SDK 加载失败"));
    };
    script.onload = () => (browser.AMap ? resolve() : failed());
    script.onerror = failed;
    document.head.append(script);
  });
}
