import type { components, paths } from "./api.generated";
import type { AccountUser } from "./account";

type Schemas = components["schemas"];
export type TaskRead = Schemas["TaskRead"];
export type TaskStatus = TaskRead["status"];
export type CurrentUser = AccountUser;
export type PendingClarification = Schemas["PendingClarification"];
export type ClarificationAnswers = Schemas["ClarificationAnswers"];
export type AgentEvent = paths["/api/v1/tasks/{task_id}/events"]["get"]["responses"][200]["content"]["text/event-stream"];
export type EventPayload = AgentEvent["payload"];
export type ResearchPlan = Schemas["ResearchPlan"];
export type ErrorInfo = Schemas["ErrorInfo"];
export type EvidenceRead = Schemas["EvidenceRead"];
export type ReportRead = Schemas["ReportRead"];

export const depthLabels: Record<NonNullable<TaskRead["brief"]["depth"]>, string> = {
  quick: "快速调研", deep: "深度调研", academic: "学术研究",
};

export const taskStatusLabels: Record<TaskStatus, string> = {
  queued: "等待启动", running: "调研进行中", needs_clarification: "待澄清",
  paused: "已暂停", completed: "已完成", failed: "调研失败",
};

const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const calendar = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium" });
export const formatTime = (value: string) => clock.format(new Date(value));
export const formatDate = (value: string) => calendar.format(new Date(value));
export const workbenchHref = (taskId: string) => `/workbench/${encodeURIComponent(taskId)}`;
export const reportHref = (taskId: string, reportId: string) => `/reports/${encodeURIComponent(taskId)}/${encodeURIComponent(reportId)}`;
export const uuidPattern = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/iu;
