import { eventTypes, readEvent } from "./agent-events";
import { demo } from "./demo";
import type { AgentEvent, EvidenceRead, ReportRead, ResearchBrief, TaskRead } from "./research";

export const mockMode = process.env.NEXT_PUBLIC_RESEARCH_MODE === "mock";
export const isMockTask = (taskId: string) => mockMode || taskId === demo.taskId;
const collection = (mock: boolean) => mock ? "/mock/api/v1/tasks" : "/api/v1/tasks";
const taskUrl = (taskId: string) => `${collection(isMockTask(taskId))}/${encodeURIComponent(taskId)}`;

async function json<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...options, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = Array.isArray(detail) ? detail[0]?.msg : detail?.message;
    throw new Error(typeof message === "string" ? message : "服务暂不可用，请重试。");
  }
  return response.json();
}

export const createTask = (brief: ResearchBrief, key: string) => json<TaskRead>(collection(mockMode), {
  method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify(brief),
});
export const readTask = (taskId: string, signal: AbortSignal) => json<TaskRead>(taskUrl(taskId), { signal });
export const readReport = (taskId: string, reportId: string, signal: AbortSignal) =>
  json<ReportRead>(`${taskUrl(taskId)}/reports/${encodeURIComponent(reportId)}`, { signal });
export const readEvidence = (taskId: string, id: string, signal: AbortSignal) =>
  json<EvidenceRead>(`${taskUrl(taskId)}/evidence/${encodeURIComponent(id)}`, { signal });

export function subscribe(
  taskId: string, after: number,
  onEvent: (event: AgentEvent) => void,
  onConnection: (state: "open" | "reconnecting") => void,
): () => void {
  const source = new EventSource(`${taskUrl(taskId)}/events?after=${after}`);
  source.onopen = () => onConnection("open");
  source.onerror = () => onConnection("reconnecting");
  for (const type of eventTypes) {
    source.addEventListener(type, (message: MessageEvent<string>) => {
      const event = readEvent(message.data, type, message.lastEventId);
      if (event) onEvent(event);
    });
  }
  return () => source.close();
}
