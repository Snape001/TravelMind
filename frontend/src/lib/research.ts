import type { components, paths } from "./api.generated";

type Schemas = components["schemas"];
export type ResearchBrief = paths["/api/v1/tasks"]["post"]["requestBody"]["content"]["application/json"];
export type TaskRead = Schemas["TaskRead"];
export type TaskStatus = TaskRead["status"];
export type CurrentUser = Schemas["CurrentUser"];
export type PendingClarification = Schemas["PendingClarification"];
export type ClarificationAnswers = Schemas["ClarificationAnswers"];
export type AgentEvent = paths["/api/v1/tasks/{task_id}/events"]["get"]["responses"][200]["content"]["text/event-stream"];
export type EventPayload = AgentEvent["payload"];
export type Assignment = Schemas["Assignment"];
export type ResearchPlan = Schemas["ResearchPlan"];
export type ErrorInfo = Schemas["ErrorInfo"];
export type EvidenceRead = Schemas["EvidenceRead"];
export type ReportRead = Schemas["ReportRead"];

export const depthOptions = [
  { value: "quick", label: "快速调研", description: "快速了解目的地要点" },
  { value: "deep", label: "深度调研", description: "多维分析与证据交叉验证" },
  { value: "academic", label: "学术研究", description: "侧重文献与来源追溯" },
] as const satisfies readonly { value: ResearchBrief["depth"]; label: string; description: string }[];

export const taskStatusLabels: Record<TaskStatus, string> = {
  queued: "等待启动", running: "调研进行中", needs_clarification: "待澄清",
  paused: "已暂停", completed: "已完成", failed: "调研失败",
};

export function prepareBrief(
  destination: string, topicText: string, requirements: string, depth: ResearchBrief["depth"],
): ResearchBrief {
  const topics = Array.from(new Set(topicText.split(/[,，、\r\n]/u).map((topic) => topic.trim()).filter(Boolean)));
  const brief = { destination: destination.trim(), topics, requirements: requirements.trim(), depth };
  if (!brief.destination || [...brief.destination].length > 200) throw new Error("请输入 1–200 字的目的地。");
  if (topics.length > 20) throw new Error("最多填写 20 个调研主题。");
  if (topics.some((topic) => [...topic].length > 200)) throw new Error("每个调研主题最多 200 字。");
  if ([...brief.requirements].length > 4000) throw new Error("个性化需求最多 4,000 字。");
  return brief;
}

const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const calendar = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium" });
export const formatTime = (value: string) => clock.format(new Date(value));
export const formatDate = (value: string) => calendar.format(new Date(value));
export const reportHref = (taskId: string, reportId: string) => `/reports/${taskId}/${reportId}`;
export const uuidPattern = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/iu;
