"use client";

import Link from "next/link";
import { ArrowLeft, ArrowUpRight, CircleAlert, FileText, GitBranch, ListChecks, MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import { EventFeed } from "./event-feed";
import { TeamGraph } from "./team-graph";
import { ExpertIcon } from "./expert-icon";
import { appendEvent, summarize } from "@/lib/agent-events";
import { demo } from "@/lib/demo";
import { expertName } from "@/lib/experts";
import { readReport, readTask, subscribe } from "@/lib/mock-client";
import { rememberReport } from "@/lib/report-history";
import { depthOptions, formatDate, reportHref, taskStatusLabels, type AgentEvent, type TaskRead } from "@/lib/research";

const assignmentLabels = { waiting: "待执行", running: "执行中", completed: "已完成", failed: "失败" };

export function Workbench({ taskId }: { taskId: string }) {
  const [attempt, setAttempt] = useState(0);
  return <WorkbenchSession key={attempt} taskId={taskId} retry={() => setAttempt((value) => value + 1)} />;
}

function WorkbenchSession({ taskId, retry }: { taskId: string; retry: () => void }) {
  const [task, setTask] = useState<TaskRead | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connection, setConnection] = useState<"connecting" | "open" | "reconnecting" | "closed">("connecting");
  const [error, setError] = useState("");
  const [agent, setAgent] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let close: (() => void) | undefined;
    readTask(taskId, controller.signal).then((record) => {
      if (controller.signal.aborted) return;
      setTask(record);
      close = subscribe(record.task_id, 0, (event) => {
        if (event.task_id !== record.task_id || event.trace_id !== record.trace_id) return;
        setEvents((previous) => appendEvent(previous, event, record));
        if (event.payload.type === "task.status" && ["completed", "failed"].includes(event.payload.status)) {
          close?.();
          setConnection("closed");
        }
      }, setConnection);
    }).catch((problem: Error) => {
      if (!controller.signal.aborted) setError(problem.message);
    });
    return () => { controller.abort(); close?.(); };
  }, [taskId]);

  const view = task && summarize(task, events);
  const archivedReportId = view && view.status === "completed" && view.reportDecision === "approved" ? view.report?.report_id : undefined;
  useEffect(() => {
    if (!archivedReportId || taskId === demo.taskId) return;
    const controller = new AbortController();
    readReport(taskId, archivedReportId, controller.signal).then((report) => {
      if (!controller.signal.aborted) rememberReport({
        taskId, reportId: report.report_id, title: report.content.title, date: report.created_at,
      });
    }).catch(() => {
      // A transient archive error does not interrupt the stream or report link.
    });
    return () => controller.abort();
  }, [taskId, archivedReportId]);

  if (!task || !view) return (
    <main className="shell inner-page">
      <div className="page-heading"><h1>调研工作台</h1></div>
      {error ? <div className="panel empty-state"><p role="alert"><CircleAlert size={18} aria-hidden />{error}</p>
        <button className="button secondary" type="button" onClick={retry}>重新加载</button><Link className="text-link" href="/">返回首页</Link></div> :
        <p className="muted" role="status">正在读取调研任务…</p>}
    </main>
  );

  const depth = depthOptions.find((option) => option.value === task.brief.depth)?.label;
  const completed = view.assignments.filter(({ status }) => status === "completed").length;
  return (
    <main className="shell inner-page workbench">
      <Link className="text-link back" href="/"><ArrowLeft size={16} aria-hidden /> 返回首页</Link>
      <div className="page-heading workbench-heading">
        <div>
          <p className="eyebrow">调研工作台 · {taskId === demo.taskId ? "示例回放" : "模拟任务"}</p>
          <h1>{task.brief.destination}调研</h1>
          <div className="meta-row"><span><MapPin size={15} aria-hidden />{task.brief.destination}</span><span>{depth}</span><span>{formatDate(task.created_at)}</span>
            <span className={`task-badge ${view.status}`} role="status"><span className={`status-dot ${view.status}`} />{taskStatusLabels[view.status]}</span>
          </div>
        </div>
        {view.report && <Link className="button primary" href={reportHref(taskId, view.report.report_id)}>
          <FileText size={17} aria-hidden />{view.reportDecision === "approved" ? "阅读调研报告" : "查看报告草稿"}<ArrowUpRight size={16} aria-hidden /></Link>}
      </div>
      {(task.brief.topics?.length || task.brief.requirements) ? <details className="task-brief">
        <summary>查看调研需求</summary>
        {task.brief.topics?.length ? <p>{task.brief.topics.join("、")}</p> : null}
        {task.brief.requirements && <p>{task.brief.requirements}</p>}
      </details> : null}
      {view.error && <p className="notice error-text" role="alert">{view.error.message}</p>}
      {view.questions.length > 0 && <section className="notice"><h2>待澄清问题</h2><ul>{view.questions.map((question, index) => <li key={index}>{question}</li>)}</ul></section>}
      <div className="workbench-grid">
        <div className="workbench-side">
          <section className="panel graph-panel" aria-labelledby="team-graph-title">
            <div className="panel-heading"><span className="icon-tile"><GitBranch size={18} aria-hidden /></span><h2 id="team-graph-title">调研组织架构</h2></div>
            <TeamGraph assignments={view.assignments} selected={agent} onSelect={setAgent} directorStatus={view.directorStatus} />
          </section>
          <section className="panel assignments-panel" aria-labelledby="assignments-title">
            <div className="panel-heading"><span className="icon-tile"><ListChecks size={18} aria-hidden /></span><h2 id="assignments-title">任务分工</h2>
              {view.assignments.length > 0 && <span className="panel-aside">{completed} / {view.assignments.length} 已完成</span>}
            </div>
            {view.plan ? <>
              <p className="plan-objective">{view.plan.objective}</p>
              <ul className="assignment-list">{view.assignments.map((assignment) => <li key={assignment.assignment_id}>
                <ExpertIcon id={assignment.agent_id} />
                <div><strong>{expertName(assignment.agent_id)}</strong><p>{assignment.objective}</p>
                  {(assignment.depends_on ?? []).length > 0 && <small>依赖 {assignment.depends_on!.map((id) => {
                    const dependency = view.assignments.find((item) => item.assignment_id === id);
                    return dependency ? expertName(dependency.agent_id) : id;
                  }).join("、")}</small>}
                </div>
                <span className={`task-badge ${assignment.status}`}>{assignmentLabels[assignment.status]}</span>
              </li>)}</ul>
            </> : <p className="plan-objective">等待调研总监发布研究计划。</p>}
          </section>
        </div>
        <EventFeed events={events} taskId={taskId} toolNames={view.toolNames} agent={agent} onSelectAgent={setAgent} connection={connection} />
      </div>
    </main>
  );
}
