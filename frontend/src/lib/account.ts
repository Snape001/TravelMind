import type { components } from "./api.generated";

type Schemas = components["schemas"];
export type AccountUser = Schemas["CurrentUser"];
export type ReportPage = Schemas["AccountReports"];
export type ProfileEdit = Schemas["ProfileEdit"];
export type ProviderLogout = Schemas["ProviderLogout"];

// Only call this with URLs issued by the same-origin authentication API.
export function providerNavigation(value: string): string {
  if (!/^https:\/\/[^/]/iu.test(value) || /[\s\\]/u.test(value)) {
    throw new Error("身份服务返回的链接无效，请重试。");
  }
  const url = new URL(value);
  if (url.username || url.password || url.hash) {
    throw new Error("身份服务返回的链接无效，请重试。");
  }
  return url.href;
}
