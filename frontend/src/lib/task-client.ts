import { eventCursor, eventTypes, readEvent } from "./agent-events";
import type { AgentEvent, CurrentUser, EvidenceRead, ReportRead, ResearchBrief, TaskRead } from "./research";
import type { TaskCommand } from "./task-lifecycle";
import { isTerminal } from "./task-lifecycle";
import { readResearchEvent, type ResearchEvent, type ResearchSource } from "./research-browser";
import type { components } from "./phase4.generated";
import type { components as LiveComponents } from "./api.generated";

const collection = "/api/v1/tasks";
const taskUrl = (taskId: string) => `${collection}/${encodeURIComponent(taskId)}`;
const reportUrl = (taskId: string, reportId: string) => `${taskUrl(taskId)}/reports/${encodeURIComponent(reportId)}`;
const auth = "/api/v1/auth";
export const loginHref = `${auth}/login`;

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string | undefined, message: string, readonly retryable: boolean) {
    super(message);
    this.name = "ApiError";
  }
}

export const missingEndpoint = (error: unknown) => error instanceof ApiError && !error.code && [404, 405].includes(error.status);

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
export const createDestinationTask = (body: components["schemas"]["DestinationTaskCreate"], key: string, csrf: string, signal: AbortSignal) =>
  json<TaskRead>(`${collection}/from-destination`, write(csrf, key, body, signal));
export const readIntake = (taskId: string, signal: AbortSignal) =>
  json<components["schemas"]["ResearchIntakeRead"]>(`${taskUrl(taskId)}/intake`, { signal });
export const readResearchSource = (taskId: string, id: string, signal: AbortSignal) =>
  json<ResearchSource>(`${taskUrl(taskId)}/research-sources/${encodeURIComponent(id)}`, { signal });
export function sendCommand(taskId: string, command: TaskCommand, key: string, csrf: string, signal?: AbortSignal) {
  const answer = "roundId" in command;
  const path = answer ?
    `${command.operation === "answer" ? "clarifications" : "questionnaires"}/${encodeURIComponent(command.roundId)}/answer` :
    command.operation;
  return json<TaskRead>(`${taskUrl(taskId)}/${path}`, write(csrf, key, answer ? command.body : undefined, signal));
}
export const readTask = (taskId: string, signal: AbortSignal) => json<TaskRead>(taskUrl(taskId), { signal });
export const readReport = (taskId: string, reportId: string, signal: AbortSignal) =>
  json<ReportRead>(reportUrl(taskId, reportId), { signal });
export const readReportMap = (taskId: string, reportId: string, signal: AbortSignal) =>
  json<LiveComponents["schemas"]["MapData"]>(`${reportUrl(taskId, reportId)}/map-data`, { signal });
export const readEvidence = (taskId: string, id: string, signal: AbortSignal) =>
  json<EvidenceRead>(`${taskUrl(taskId)}/evidence/${encodeURIComponent(id)}`, { signal });
export const readTaskRoutes = (taskId: string, signal: AbortSignal) =>
  json<LiveComponents["schemas"]["TravelRoutesRead"]>(`${taskUrl(taskId)}/routes`, { signal });

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

export type ResearchConnection = "connecting" | "open" | "reconnecting" | "closed" | "unavailable";

export function subscribeResearch(
  task: TaskRead, onEvent: (event: ResearchEvent) => void,
  onConnection: (state: ResearchConnection) => void, onAccessError: (error: ApiError) => void,
): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  const cursor = eventCursor(task, 0);
  let terminalReplay = isTerminal(task.status);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let delay = 500;

  async function connect() {
    if (signal.aborted) return;
    try {
      const response = await request(`${taskUrl(task.task_id)}/research-events?after=${cursor.sequence}`, {
        signal, headers: { Accept: "text/event-stream" },
      });
      if (signal.aborted) return;
      if (!response.headers.get("Content-Type")?.startsWith("text/event-stream") || !response.body) {
        throw new Error("来源事件响应格式不正确。");
      }
      onConnection("open");
      await consumeResearchStream(response.body, (event) => {
        if (!signal.aborted && cursor.accept(event)) {
          delay = 500;
          onEvent(event);
        }
      });
      if (signal.aborted) return;
      // Drain the independent stream after terminal status, including records published after the core stream closed.
      const snapshot = await readTask(task.task_id, signal);
      if (signal.aborted) return;
      if (isTerminal(snapshot.status)) {
        if (terminalReplay) {
          onConnection("closed");
          return;
        }
        terminalReplay = true;
        void connect();
        return;
      }
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && [401, 403, 404, 405].includes(error.status)) {
        if (!missingEndpoint(error)) onAccessError(error);
        onConnection("unavailable");
        return;
      }
    }
    onConnection("reconnecting");
    timer = setTimeout(connect, delay);
    delay = Math.min(delay * 2, 10000);
  }
  void connect();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}

async function consumeResearchStream(body: ReadableStream<Uint8Array>, accept: (event: ResearchEvent) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      pending += decoder.decode(value, { stream: true });
      const frames = pending.split(/\r?\n\r?\n/u);
      pending = frames.pop() ?? "";
      for (const frame of frames) {
        let id = "";
        let name = "";
        const data: string[] = [];
        for (const line of frame.split(/\r?\n/u)) {
          if (line.startsWith("id:")) id = line.slice(3).replace(/^ /u, "");
          else if (line.startsWith("event:")) name = line.slice(6).replace(/^ /u, "");
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /u, ""));
        }
        if (!data.length) continue;
        const event = readResearchEvent(data.join("\n"), name, id);
        if (event) accept(event);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
