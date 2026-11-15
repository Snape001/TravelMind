import { eventCursor, eventTypes, readEvent } from "./agent-events";
import type { DestinationTaskCreate, Intake, Submission } from "./intake";
import type { AgentEvent, CurrentUser, EvidenceRead, ReportRead, ResearchBrief, TaskRead } from "./research";
import type { TaskCommand } from "./task-lifecycle";

const collection = "/api/v1/tasks";
const taskUrl = (taskId: string) => `${collection}/${encodeURIComponent(taskId)}`;
const auth = "/api/v1/auth";
export const loginHref = `${auth}/login`;

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string | undefined, message: string, readonly retryable: boolean) {
    super(message);
    this.name = "ApiError";
  }
}

async function request(url: string, options: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, { ...options, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = Array.isArray(detail) ? detail[0]?.msg : detail?.message;
    throw new ApiError(response.status, typeof detail?.code === "string" ? detail.code : undefined,
      typeof message === "string" ? message : "服务暂不可用，请重试。", detail?.retryable === true);
  }
  return response;
}

async function json<T>(url: string, options?: RequestInit): Promise<T> {
  return (await request(url, options)).json();
}

function write(csrf: string, key?: string, body?: object, signal?: AbortSignal): RequestInit {
  const headers = new Headers({ "X-CSRF-Token": csrf });
  if (key) headers.set("Idempotency-Key", key);
  if (body) headers.set("Content-Type", "application/json");
  return { method: "POST", headers, body: body && JSON.stringify(body), signal };
}

export const readSession = (signal?: AbortSignal) => json<CurrentUser>(`${auth}/me`, { signal });
export const logout = (csrf: string) => request(`${auth}/logout`, write(csrf));
export const createTask = (brief: ResearchBrief, key: string, csrf: string, signal?: AbortSignal) =>
  json<TaskRead>(collection, write(csrf, key, brief, signal));
export const createDestinationTask = (body: DestinationTaskCreate, key: string, csrf: string, signal?: AbortSignal) =>
  json<TaskRead>(`${collection}/from-destination`, write(csrf, key, body, signal));
export const readIntake = (taskId: string, signal?: AbortSignal) =>
  json<Intake>(`${taskUrl(taskId)}/intake`, { signal });
export const answerQuestionnaire = (taskId: string, roundId: string, body: Submission, key: string, csrf: string, signal?: AbortSignal) =>
  json<TaskRead>(`${taskUrl(taskId)}/questionnaires/${encodeURIComponent(roundId)}/answer`, write(csrf, key, body, signal));
export function sendCommand(taskId: string, command: TaskCommand, key: string, csrf: string, signal?: AbortSignal) {
  const path = command.operation === "answer" ? `clarifications/${encodeURIComponent(command.roundId)}/answer` : command.operation;
  return json<TaskRead>(`${taskUrl(taskId)}/${path}`, write(csrf, key, command.operation === "answer" ? command.body : undefined, signal));
}
export const readTask = (taskId: string, signal: AbortSignal) => json<TaskRead>(taskUrl(taskId), { signal });
export const readReport = (taskId: string, reportId: string, signal: AbortSignal) =>
  json<ReportRead>(`${taskUrl(taskId)}/reports/${encodeURIComponent(reportId)}`, { signal });
export const readEvidence = (taskId: string, id: string, signal: AbortSignal) =>
  json<EvidenceRead>(`${taskUrl(taskId)}/evidence/${encodeURIComponent(id)}`, { signal });

export function subscribe(
  task: Pick<TaskRead, "task_id" | "trace_id">, after: number,
  onEvent: (event: AgentEvent) => void,
  onConnection: (state: "open" | "reconnecting") => void,
): () => void {
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cursor = eventCursor(task, after);
  let delay = 500;
  let stopped = false;
  function connect() {
    if (stopped) return;
    const active = new EventSource(`${taskUrl(task.task_id)}/events?after=${cursor.sequence}`);
    source = active;
    active.onopen = () => { if (!stopped && active === source) onConnection("open"); };
    active.onerror = () => {
      if (stopped || active !== source) return;
      source = undefined;
      active.close();
      onConnection("reconnecting");
      if (!stopped) timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    };
    for (const type of eventTypes) {
      active.addEventListener(type, (message: MessageEvent<string>) => {
        if (stopped || active !== source) return;
        const event = readEvent(message.data, type, message.lastEventId);
        if (event && cursor.accept(event)) {
          delay = 500;
          onEvent(event);
        }
      });
    }
  }
  connect();
  return () => {
    stopped = true;
    clearTimeout(timer);
    source?.close();
    source = undefined;
  };
}
