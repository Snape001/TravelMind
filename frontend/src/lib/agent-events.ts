import type { AgentEvent, ErrorInfo, EventPayload, ResearchPlan, TaskRead } from "./research";
import { taskStatusLabels } from "./research";

export const eventTypes = [
  "task.status", "task.clarification", "plan.ready", "agent.started", "agent.message",
  "agent.finished", "tool.started", "tool.finished", "evidence.added", "report.ready", "report.reviewed",
] as const satisfies EventPayload["type"][];

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");

function validPayload(payload: Record<string, unknown>): boolean {
  const { type } = payload;
  if (type === "task.status") return typeof payload.status === "string" && Object.hasOwn(taskStatusLabels, payload.status);
  if (type === "task.clarification") return strings(payload.questions);
  if (type === "plan.ready") {
    const { plan } = payload;
    return record(plan) && typeof plan.objective === "string" && Array.isArray(plan.assignments) &&
      plan.assignments.every((item) => record(item) && typeof item.assignment_id === "string" &&
        typeof item.agent_id === "string" && typeof item.objective === "string" &&
        (item.depends_on === undefined || strings(item.depends_on)));
  }
  if (typeof payload.agent_id !== "string" || typeof payload.run_id !== "string") return false;
  switch (type) {
    case "agent.started": return payload.assignment_id == null || typeof payload.assignment_id === "string";
    case "agent.message": return typeof payload.message === "string";
    case "agent.finished": return payload.status === "completed" || payload.status === "failed";
    case "tool.started": return typeof payload.tool_name === "string" && typeof payload.tool_call_id === "string";
    case "tool.finished": return typeof payload.tool_call_id === "string" && typeof payload.duration_ms === "number" &&
      (payload.status === "completed" || payload.status === "failed");
    case "evidence.added": return strings(payload.evidence_ids);
    case "report.ready": return typeof payload.report_id === "string" && typeof payload.version === "number";
    case "report.reviewed": return typeof payload.report_id === "string" &&
      (payload.decision === "approved" || payload.decision === "revision_required");
    default: return false;
  }
}

export function readEvent(data: string, name: string, id: string): AgentEvent | null {
  try {
    const event: unknown = JSON.parse(data);
    if (!record(event) || event.version !== 1 || typeof event.event_id !== "string" ||
      typeof event.task_id !== "string" || typeof event.trace_id !== "string" ||
      typeof event.sequence !== "number" || !Number.isSafeInteger(event.sequence) || event.sequence < 1 ||
      String(event.sequence) !== id || typeof event.occurred_at !== "string" || !Number.isFinite(Date.parse(event.occurred_at)) ||
      !record(event.payload) || event.payload.type !== name || !validPayload(event.payload)) return null;
    const { error } = event.payload;
    if (error != null && (!record(error) || typeof error.message !== "string" ||
      typeof error.code !== "string" || typeof error.retryable !== "boolean")) return null;
    return event as AgentEvent;
  } catch {
    return null;
  }
}

export function eventCursor(task: Pick<TaskRead, "task_id" | "trace_id">, after: number) {
  let sequence = after;
  const seen = new Set<string>();
  return {
    get sequence() { return sequence; },
    accept(event: AgentEvent) {
      if (event.task_id !== task.task_id || event.trace_id !== task.trace_id ||
        event.sequence <= sequence || seen.has(event.event_id)) return false;
      sequence = event.sequence;
      seen.add(event.event_id);
      return true;
    },
  };
}

export function summarize(task: TaskRead, events: AgentEvent[]) {
  let status = task.status;
  let error: ErrorInfo | null = null;
  let plan: ResearchPlan | null = null;
  let report: Extract<EventPayload, { type: "report.ready" }> | null = null;
  let questions: string[] = [];
  const decisions = new Map<string, Extract<EventPayload, { type: "report.reviewed" }>["decision"]>();
  const latestAssignmentRuns = new Map<string, string>();
  const latestAgentRuns = new Map<string, string>();
  const runStatus = new Map<string, "running" | "completed" | "failed">();
  const toolNames = new Map<string, string>();

  for (const { payload } of events) {
    switch (payload.type) {
      case "task.status":
        status = payload.status;
        error = payload.error ?? null;
        if (status !== "needs_clarification") questions = [];
        break;
      case "task.clarification": questions = payload.questions; break;
      case "plan.ready": plan = payload.plan; break;
      case "agent.started":
        runStatus.set(payload.run_id, "running");
        latestAgentRuns.set(payload.agent_id, payload.run_id);
        if (payload.assignment_id) latestAssignmentRuns.set(payload.assignment_id, payload.run_id);
        break;
      case "agent.finished": runStatus.set(payload.run_id, payload.status); break;
      case "tool.started": toolNames.set(payload.tool_call_id, payload.tool_name); break;
      case "report.ready":
        if (!report || payload.version >= report.version) report = payload;
        break;
      case "report.reviewed": decisions.set(payload.report_id, payload.decision); break;
    }
  }

  const assignments = (plan?.assignments ?? []).map((assignment) => ({
    ...assignment,
    status: runStatus.get(latestAssignmentRuns.get(assignment.assignment_id) ?? "") ?? "waiting" as const,
  }));
  const directorRun = latestAgentRuns.get("research_director");
  const directorStatus = runStatus.get(directorRun ?? "") ?? "waiting" as const;
  return { status, error, plan, report, questions, assignments, toolNames, directorStatus, reportDecision: report && decisions.get(report.report_id) };
}
