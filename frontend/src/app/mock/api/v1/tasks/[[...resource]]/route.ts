import { demoTask, eventsAfter, streamResearch, taskAt } from "@/lib/mock-research";
import { fixtureResearch, FixtureTask } from "@/lib/lifecycle-fixture";
import { checkFixtureWrite, fixtureError as error, fixtureSession } from "@/lib/fixture-session";
import { uuidPattern as uuid, type ResearchBrief, type TaskRead } from "@/lib/research";

export const runtime = "nodejs";

type Context = { params: Promise<{ resource?: string[] }> };
const archive = new Map<string, { owner: string; task: FixtureTask }>();
const requests = new Map<string, { fingerprint: string; taskId: string }>();
const invalid = (location: (string | number)[], msg: string) =>
  Response.json({ detail: [{ loc: location, msg, type: "value_error" }] }, { status: 422 });

function taskResponse(task: TaskRead, created: boolean) {
  return Response.json(task, {
    status: created ? 201 : 200,
    headers: { Location: `/mock/api/v1/tasks/${task.task_id}` },
  });
}

export async function POST(request: Request, context: Context) {
  if (process.env.NEXT_PUBLIC_RESEARCH_MODE !== "mock") return error(404, "NOT_FOUND", "页面不存在。");
  const user = fixtureSession(request);
  if (!user) return error(401, "UNAUTHENTICATED", "请先登录。");
  if (!checkFixtureWrite(request, user)) return error(403, "CSRF_INVALID", "会话校验失败，请重试。");
  const resource = (await context.params).resource ?? [];
  const key = request.headers.get("Idempotency-Key");
  if (resource.length) {
    const [id, action, roundId, suffix] = resource;
    const stored = archive.get(id.toLowerCase());
    if (!stored || stored.owner !== user.user_id) return error(404, "TASK_NOT_FOUND", "调研任务不存在。");
    const operation = resource.length === 2 && (action === "pause" || action === "resume") ? action :
      resource.length === 4 && action === "clarifications" && suffix === "answer" ? "answer" : null;
    if (!operation) return error(404, "NOT_FOUND", "页面不存在。");
    if (operation !== "answer" && await request.text()) return error(422, "VALIDATION_ERROR", "该操作不接受请求体。");
    const input = operation === "answer" ? { roundId, body: await request.json().catch(() => null) } : null;
    const result = stored.task.command(operation, key ?? "", input);
    if (result.task) return Response.json(result.task, { status: result.status, headers: { "Cache-Control": "no-store" } });
    return error(result.status, result.error, {
      VALIDATION_ERROR: "请求格式或答复不符合要求。",
      CLARIFICATION_STALE: "这轮澄清已结束，请查看最新任务状态。",
      IDEMPOTENCY_CONFLICT: "幂等键已用于其他请求。",
      TASK_STATE_CONFLICT: "任务状态不支持该操作。",
    }[result.error]);
  }
  if (key !== null && !/^[!-~]{1,128}$/u.test(key)) return invalid(["header", "Idempotency-Key"], "无效的幂等键。");

  const body: unknown = await request.json().catch(() => null);
  if (typeof body !== "object" || body === null || Array.isArray(body)) return invalid(["body"], "请求体应为 JSON 对象。");
  const fields = body as Record<string, unknown>;
  const unexpected = Object.keys(fields).find((field) => !["destination", "topics", "requirements", "depth"].includes(field));
  if (unexpected) return invalid(["body", unexpected], "请求包含未定义字段。");
  const { destination, topics = [], requirements = "", depth = "quick" } = fields;
  if (typeof destination !== "string" || !destination.trim() || [...destination.trim()].length > 200) {
    return invalid(["body", "destination"], "请输入 1–200 字的目的地。");
  }
  if (!Array.isArray(topics) || topics.length > 20 ||
    topics.some((topic) => typeof topic !== "string" || !topic.trim() || [...topic.trim()].length > 200)) {
    return invalid(["body", "topics"], "最多 20 个主题，每项 1–200 字。");
  }
  if (typeof requirements !== "string" || [...requirements.trim()].length > 4000) {
    return invalid(["body", "requirements"], "需求最多 4,000 字。");
  }
  if (depth !== "quick" && depth !== "deep" && depth !== "academic") return invalid(["body", "depth"], "无效的调研深度。");
  const brief: ResearchBrief = { destination: destination.trim(), topics: topics.map((topic: string) => topic.trim()), requirements: requirements.trim(), depth };
  const fingerprint = JSON.stringify(brief);
  const requestKey = key && `${user.user_id}:${key}`;
  const previous = requestKey && requests.get(requestKey);
  if (previous) {
    if (previous.fingerprint !== fingerprint) return error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求。");
    return taskResponse(archive.get(previous.taskId)!.task.snapshot(), false);
  }
  const fixture = new FixtureTask(fixtureResearch(brief));
  const task = fixture.snapshot();
  archive.set(task.task_id, { owner: user.user_id, task: fixture });
  if (requestKey) requests.set(requestKey, { fingerprint, taskId: task.task_id });
  return taskResponse(task, true);
}

export async function GET(request: Request, context: Context) {
  const [taskId, resource, resourceId, ...rest] = (await context.params).resource ?? [];
  if (!taskId || rest.length || (resource && !["events", "reports", "evidence"].includes(resource)) ||
    (resource === "events" && resourceId) || ((resource === "reports" || resource === "evidence") && !resourceId)) {
    return error(404, "NOT_FOUND", "页面不存在。");
  }
  if (!uuid.test(taskId)) return invalid(["path", "task_id"], "无效的任务 ID。");
  const example = taskId.toLowerCase() === demoTask.task.task_id;
  const user = example ? undefined : fixtureSession(request);
  if (!example && !user) return error(401, "UNAUTHENTICATED", "请先登录。");
  const stored = example || process.env.NEXT_PUBLIC_RESEARCH_MODE !== "mock" ? undefined : archive.get(taskId.toLowerCase());
  if (!example && (!stored || stored.owner !== user?.user_id)) return error(404, "TASK_NOT_FOUND", "调研任务不存在。");
  const research = stored?.task.research ?? demoTask;
  if (!resource) return Response.json(stored?.task.snapshot() ?? taskAt(research), { headers: { "Cache-Control": "no-store" } });

  if (resource === "events") {
    const header = request.headers.get("Last-Event-ID");
    const query = new URL(request.url).searchParams.get("after");
    for (const [value, location] of [[header, ["header", "last-event-id"]], [query, ["query", "after"]]] as const) {
      if (value !== null && (!/^\d+$/u.test(value) || !Number.isSafeInteger(Number(value)))) {
        return invalid([...location], "游标须为非负安全整数。");
      }
    }
    const eventsAt = stored && ((now: number) => fixtureSession(request) ? stored.task.eventsAt(now) : null);
    return new Response(streamResearch(research, Number(header ?? query ?? 0), request.signal, eventsAt), {
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no" },
    });
  }

  const idField = resource === "reports" ? "report_id" : "evidence_id";
  if (!uuid.test(resourceId)) return invalid(["path", idField], "无效的资源 ID。");
  const id = resourceId.toLowerCase();
  const now = Date.now();
  const visible = (stored?.task.eventsAt(now) ?? eventsAfter(research, 0, now)).some(({ payload }) => (
    resource === "reports" ? payload.type === "report.ready" && payload.report_id === id :
      payload.type === "evidence.added" && payload.evidence_ids.includes(id)
  ));
  const item = visible && (resource === "reports" ?
    research.reports.find((report) => report.report_id === id) :
    research.evidence.find((source) => source.evidence_id === id));
  if (item) return Response.json(item, { headers: { "Cache-Control": "no-store" } });
  return resource === "reports" ? error(404, "REPORT_NOT_FOUND", "该任务的报告暂不可用。") :
    error(404, "EVIDENCE_NOT_FOUND", "该任务的证据暂不可用。");
}
