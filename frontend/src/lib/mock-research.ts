import { encodeEvent } from "./agent-events";
import { demo } from "./demo";
import { expertName } from "./experts";
import type { AgentEvent, Assignment, EventPayload, EvidenceRead, ReportRead, ResearchBrief, TaskRead } from "./research";

export type MockResearch = {
  task: TaskRead;
  events: AgentEvent[];
  evidence: EvidenceRead[];
  reports: ReportRead[];
};

function selectSpecialists(brief: ResearchBrief): string[] {
  const text = [...(brief.topics ?? []), brief.requirements].join(" ");
  const focus: [RegExp, string][] = [
    [/历史|文化|古城|古迹|非遗/u, "history_culture"],
    [/自然|徒步|气候|生态|山水/u, "natural_geography"],
    [/景点|博物馆|景区|游览/u, "tourism_resources"],
    [/评价|舆情|体验/u, "social_sentiment"],
    [/文献|论文|学术/u, "literature_research"],
    [/公告|政策|官方/u, "internet_research"],
    [/交通|住宿|路线|预算|亲子/u, "travel_planning"],
  ];
  const candidates = focus.filter(([pattern]) => pattern.test(text)).map(([, id]) => id);
  if (brief.depth === "academic") candidates.unshift("literature_research");
  return [...new Set([...candidates, "internet_research", "travel_planning", "tourism_resources"])]
    .slice(0, brief.depth === "quick" ? 2 : brief.depth === "deep" ? 3 : 4);
}

export function buildResearch(brief: ResearchBrief, start = Date.now(), archive = false): MockResearch {
  let archiveId = 10;
  const id = () => archive ? `856d05aa-7d53-4fe7-aefa-${(archiveId++).toString(16).padStart(12, "0")}` :
    globalThis.crypto.randomUUID();
  const date = (offset = 0) => new Date(start + offset).toISOString();
  const task: TaskRead = {
    task_id: archive ? demo.taskId : id(),
    trace_id: archive ? "856d05aa-7d53-4fe7-aefa-d17e6a84c002" : id(),
    brief, status: "queued", created_at: date(), updated_at: date(),
  };
  const { destination, topics, requirements } = brief;
  const specialists = selectSpecialists(brief);
  const assignments: Assignment[] = specialists.map((agent_id) => ({
    assignment_id: id(), agent_id,
    objective: `从${expertName(agent_id).replace("专家", "")}角度梳理${destination}的相关资料与待核信息。`,
  }));
  const writer = { assignment_id: id(), agent_id: "report_writer", objective: "整合研究成果并生成报告。", depends_on: assignments.map((item) => item.assignment_id) };
  const reviewer = { assignment_id: id(), agent_id: "reviewer", objective: "核对引用与适用条件。", depends_on: [writer.assignment_id] };
  const evidence: EvidenceRead[] = specialists.map((agent, index) => {
    const heritage = archive && agent === "history_culture";
    return {
      task_id: task.task_id, evidence_id: id(), source_type: heritage ? "official" : "web",
      source_title: heritage ? "联合国教科文组织 · 泉州世界遗产档案" : `${destination} · ${expertName(agent)}资料样例`,
      source_url: heritage ? "https://whc.unesco.org/en/list/1561/" : null,
      source_locator: heritage ? "UNESCO World Heritage List, no. 1561" : `模拟资料样例 ${index + 1}`,
      retrieved_at: date(4000),
      excerpt: heritage ? "遗产档案介绍了宋元时期泉州海洋贸易相关的遗产点。" :
        `演示摘录。真实调研时需补充${destination}的原始资料、发布时间及适用条件。`,
    };
  });
  const firstReportId = archive ? "856d05aa-7d53-4fe7-aefa-d17e6a84c003" : id();
  const finalReportId = archive ? demo.reportId : id();
  const title = archive ? demo.title : `${destination}目的地调研报告`;
  const topicText = topics?.length ? topics.join("、") : "目的地概况与出行安排";
  const reports: ReportRead[] = [firstReportId, finalReportId].map((report_id, index) => ({
    task_id: task.task_id, report_id, version: index + 1, created_at: date(index === 0 ? 9900 : 13900),
    content: {
      title,
      sections: [
        {
          heading: "调研概览", author_agent_ids: ["research_director"],
          body: `本次围绕${destination}的${topicText}展开模拟调研。${requirements ? `出行需求为「${requirements}」。` : ""}\n\n报告展示专家分工、结论组织和证据追溯的完整流程。`,
        },
        {
          heading: archive ? "古城文化与遗产线索" : "研究主题与资料线索",
          author_agent_ids: specialists.slice(0, 2), evidence_ids: evidence.slice(0, 2).map((source) => source.evidence_id),
          body: archive ? "可从宋元时期的海上贸易切入，结合 UNESCO 遗产档案了解泉州的遗产点。周末行程可以按主题选取少量地点，为步行和现场参观预留时间。" :
            `按${topicText}整理待查问题。优先查阅目的地官方资料，并与其他来源对照适用范围与发布时间。`,
        },
        {
          heading: "行程安排与待核事项", author_agent_ids: ["report_writer", ...specialists.slice(2)],
          evidence_ids: evidence.slice(2).map((source) => source.evidence_id),
          body: `先选定主要游览区域，再按步行与公共交通的可达性安排备选地点。当前示例中的实时开放、交通与预约信息仍待核实。${index ? "\n\n审核修订补充了出发前检查项：开放时间、预约规则和末班交通。" : ""}`,
        },
      ],
    },
  }));
  const events: AgentEvent[] = [];
  const add = (offset: number, payload: EventPayload) => {
    events.push({ version: 1, event_id: id(), task_id: task.task_id, trace_id: task.trace_id,
      sequence: events.length + 1, occurred_at: date(offset), payload });
  };
  const run = (agent_id: string) => ({ agent_id, run_id: id() });
  const director = run("research_director");

  add(0, { type: "task.status", status: "queued" });
  add(450, { type: "task.status", status: "running" });
  add(650, { type: "agent.started", ...director });
  add(1100, { type: "agent.message", ...director, message: `已收到关于${destination}的调研需求，将按「${topicText}」组织专家分工。` });
  add(1800, { type: "plan.ready", plan: { objective: `围绕${topicText}形成${destination}的可追溯调研报告。`, assignments: [...assignments, writer, reviewer] } });
  add(2000, { type: "agent.finished", ...director, status: "completed" });

  const specialistRuns = assignments.map((assignment) => ({ ...run(assignment.agent_id), assignment_id: assignment.assignment_id }));
  const toolCalls = specialistRuns.map(() => id());
  specialistRuns.forEach(({ assignment_id, ...current }, index) => add(2400 + index * 120, { type: "agent.started", ...current, assignment_id }));
  specialistRuns.forEach(({ agent_id, run_id }, index) => add(3250 + index * 170, {
    type: "tool.started", agent_id, run_id, tool_call_id: toolCalls[index],
    tool_name: agent_id === "literature_research" ? "文献检索" : agent_id === "travel_planning" ? "交通资料检索" : "网页资料检索",
  }));
  specialistRuns.forEach(({ agent_id, run_id }, index) => {
    const offset = 4500 + index * 590;
    add(offset, { type: "tool.finished", agent_id, run_id, tool_call_id: toolCalls[index], status: "completed", duration_ms: offset - (3250 + index * 170) });
    add(offset + 120, { type: "evidence.added", agent_id, run_id, evidence_ids: [evidence[index].evidence_id] });
    add(offset + 280, { type: "agent.message", agent_id, run_id, message: index === 0 ?
      `我整理了${destination}的第一组资料。请其他专家核对这些线索的适用季节与出行条件。` :
      "收到。会在结论中标明资料适用范围，并把实时开放和预约信息列为待核事项。" });
    add(offset + 430, { type: "agent.finished", agent_id, run_id, status: "completed" });
  });

  const writing = run(writer.agent_id);
  const reviewing = run(reviewer.agent_id);
  const revision = run(writer.agent_id);
  const recheck = run(reviewer.agent_id);
  add(7700, { type: "agent.started", ...writing, assignment_id: writer.assignment_id });
  add(8800, { type: "agent.message", ...writing, message: "正在合并专家结论，为每个章节附上署名和引用。" });
  add(9900, { type: "report.ready", ...writing, report_id: firstReportId, version: 1 });
  add(10100, { type: "agent.finished", ...writing, status: "completed" });
  add(10400, { type: "agent.started", ...reviewing, assignment_id: reviewer.assignment_id });
  add(11100, { type: "agent.message", ...reviewing, message: "请补充出发前应核实的开放时间、预约规则和交通条件。" });
  add(11400, { type: "report.reviewed", ...reviewing, report_id: firstReportId, decision: "revision_required" });
  add(11600, { type: "agent.finished", ...reviewing, status: "completed" });
  add(12200, { type: "agent.started", ...revision, assignment_id: writer.assignment_id });
  add(13900, { type: "report.ready", ...revision, report_id: finalReportId, version: 2 });
  add(14100, { type: "agent.finished", ...revision, status: "completed" });
  add(14500, { type: "agent.started", ...recheck, assignment_id: reviewer.assignment_id });
  add(15200, { type: "report.reviewed", ...recheck, report_id: finalReportId, decision: "approved" });
  add(15400, { type: "agent.finished", ...recheck, status: "completed" });
  add(15700, { type: "task.status", status: "completed" });
  return { task, events, evidence, reports };
}

export const demoTask = buildResearch(
  { destination: demo.destination, topics: ["古城文化", "公共交通"], requirements: "周末两天，以步行和公共交通为主。", depth: "deep" },
  Date.parse(demo.date),
  true,
);

export function eventsAfter(research: MockResearch, cursor: number, now = Date.now()): AgentEvent[] {
  return research.events.filter((event) => event.sequence > cursor && Date.parse(event.occurred_at) <= now);
}

export function taskAt(research: MockResearch, now = Date.now()): TaskRead {
  const available = eventsAfter(research, 0, now);
  const status = available.findLast(({ payload }) => payload.type === "task.status")?.payload;
  return { ...research.task, status: status?.type === "task.status" ? status.status : research.task.status,
    updated_at: available.at(-1)?.occurred_at ?? research.task.updated_at };
}

export function streamResearch(research: MockResearch, after: number, signal: AbortSignal): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let cancel = () => {};
  return new ReadableStream({
    start(controller) {
      let cursor = after;
      let timer: ReturnType<typeof setTimeout>;
      let lastFrame = Date.now();
      let closed = false;
      const cleanUp = () => {
        closed = true;
        clearTimeout(timer);
        signal.removeEventListener("abort", finish);
      };
      function finish() {
        if (closed) return;
        cleanUp();
        controller.close();
      }
      cancel = cleanUp;
      function tick() {
        const now = Date.now();
        for (const event of eventsAfter(research, cursor, now)) {
          controller.enqueue(encoder.encode(encodeEvent(event)));
          cursor = event.sequence;
          lastFrame = now;
        }
        if (now - lastFrame >= 15000) {
          controller.enqueue(encoder.encode(": heartbeat\n\n"));
          lastFrame = now;
        }
        const next = research.events.find((event) => event.sequence > cursor);
        timer = setTimeout(tick, next ? Math.max(20, Date.parse(next.occurred_at) - now) : 15000);
      }
      signal.addEventListener("abort", finish, { once: true });
      if (signal.aborted) finish();
      else tick();
    },
    cancel() { cancel(); },
  });
}
