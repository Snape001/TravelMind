import type { components } from "./api.generated";

// Account additions from docs/account-auth-contract.md, pending the backend OpenAPI release.
export type AccountUser = components["schemas"]["CurrentUser"] & {
  email?: string | null;
  email_verified?: boolean | null;
  account_management_enabled?: boolean;
};

export type ReportSummary = {
  task_id: string;
  report_id: string;
  version: number;
  title: string;
  published_at: string;
};

export type ReportPage = {
  items: ReportSummary[];
  next_cursor: string | null;
};

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
