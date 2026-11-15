"use client";

import Link from "next/link";
import { ArrowLeft, ArrowUpRight, CircleAlert, FileText, ListChecks, MapPin, UsersRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EventFeed } from "./event-feed";
import { assignmentLabels, TeamGraph } from "./team-graph";
import { ExpertAvatar, ExpertLabel } from "./expert-identity";
import { summarize } from "@/lib/agent-events";
import { demo, demoResearch } from "@/lib/demo";
import { expertName } from "@/lib/experts";
import { readReport } from "@/lib/task-client";
import { rememberTask } from "@/lib/task-history";
import { depthLabels, formatDate, reportHref, taskStatusLabels, type CurrentUser, type ReportRead } from "@/lib/research";
import { projectResearch, type ResearchEvent } from "@/lib/research-browser";
import { ResearchBrowser, type LegacyEvidence, type SourceSelection } from "./research-browser";
import { WorkspacePanes } from "./workspace-panes";
import { SessionPrompt, useSession } from "./session";
import { TaskControls } from "./task-controls";
import { useTaskSession } from "./use-task-session";
import { WorkbenchRoutePreview } from "./workbench-route-preview";

const noResearchEvents: ResearchEvent[] = [];

export function Workbench({ taskId }: { taskId: string }) {
  const { session } = useSession();
  const [attempt, setAttempt] = useState(0);
  const user = session.user;
  if (!user && taskId !== demo.taskId) return <main className="shell inner-page">
    <div className="page-heading"><h1>调研工作台</h1></div><SessionPrompt />
  </main>;
  return <WorkbenchSession key={`${taskId}:${user?.user_id ?? "demo"}:${attempt}`} taskId={taskId} user={user} retry={() => setAttempt((value) => value + 1)} />;
}

function WorkbenchSession({ taskId, user, retry }: { taskId: string; user?: CurrentUser; retry: () => void }) {
  const { handleError } = useSession();
  const example = taskId === demo.taskId;
  const live = useTaskSession(example ? null : taskId, handleError);
  const { error, accept, refresh } = live;
  const task = example ? demoResearch.task : live.task;
  const events = example ? demoResearch.events : live.events;
  const connection = example ? "closed" : live.connection;
  const [agent, setAgent] = useState("");
  const [selected, setSelected] = useState<SourceSelection | null>(null);
  const [reportSnapshot, setReportSnapshot] = useState<ReportRead>();
  const userId = user?.user_id;
  const taskLoaded = task !== null;

  useEffect(() => {
    if (taskLoaded && userId && !example) rememberTask(userId, taskId);
  }, [taskLoaded, taskId, userId, example]);

  const view = useMemo(() => task && summarize(task, events), [task, events]);
  const publishedId = view?.published?.report_id;
  const extension = example ? noResearchEvents : live.research;
  const research = useMemo(() => projectResearch(extension, publishedId), [extension, publishedId]);
  const evidence = useMemo(() => {
    const index = new Map<string, LegacyEvidence>();
    for (const { payload } of events) {
      if (payload.type !== "evidence.added") continue;
      for (const id of payload.evidence_ids) {
        if (!research.evidenceIds.has(id) && !index.has(id)) index.set(id, { evidenceId: id, agentId: payload.agent_id });
      }
    }
    return [...index.values()];
  }, [events, research.evidenceIds]);
  useEffect(() => {
    if (!publishedId || !userId || example) return;
    const controller = new AbortController();
    readReport(taskId, publishedId, controller.signal).then((report) => {
      if (!controller.signal.aborted) setReportSnapshot(report);
    }).catch((problem: unknown) => {
      if (!controller.signal.aborted) handleError(problem);
    });
    return () => controller.abort();
  }, [taskId, publishedId, userId, example, handleError]);

  if (!task || !view) return (
    <main className="shell inner-page">
      <div className="page-heading"><h1>调研工作台</h1></div>
      {error ? <div className="panel empty-state"><p role="alert"><CircleAlert size={18} aria-hidden />{error}</p>
        <button className="button secondary" type="button" onClick={retry}>重新加载</button><Link className="text-link" href="/">返回首页</Link></div> :
        <p className="muted" role="status">正在读取调研任务…</p>}
    </main>
  );

  const depth = task.brief.depth && depthLabels[task.brief.depth];
  const completed = view.assignments.filter(({ status }) => status === "completed").length;
  const publishedReport = example ? demoResearch.reports.find(({ report_id }) => report_id === publishedId) : reportSnapshot;
  const citedIds = publishedReport && publishedReport.report_id === publishedId ?
    new Set(publishedReport.content.sections.flatMap(({ evidence_ids }) => evidence_ids ?? [])) : null;
  const reportLink = view.published ?? view.report;
  const controls = user && !example && <TaskControls task={task} user={user} accept={accept} refresh={refresh} onError={handleError} />;
  return (
    <main className="workbench workspace">
      <header className="workspace-heading">
        <div>
          <Link className="text-link workspace-back" href="/workbench"><ArrowLeft size={14} aria-hidden /> 调研历史</Link>
          <div className="workspace-title"><MapPin size={23} aria-hidden /><h1>{task.brief.destination}调研</h1></div>
          <div className="meta-row"><span>{depth}</span><span>{formatDate(task.created_at)}</span>
            {example && <span>示例回放</span>}
            <span className={`task-badge ${task.status}`} role="status"><span className={`status-dot ${task.status}`} />{taskStatusLabels[task.status]}</span>
          </div>
        </div>
        <div className="workspace-heading-actions">
          {(task.brief.topics?.length || task.brief.requirements) ? <details className="task-brief">
            <summary>查看调研需求</summary>
            <div className="brief-popover">
              {task.brief.topics?.length ? <p>{task.brief.topics.join("、")}</p> : null}
              {task.brief.requirements && <p>{task.brief.requirements}</p>}
            </div>
          </details> : null}
          {reportLink && <Link className="button primary" href={reportHref(taskId, reportLink.report_id)}>
            <FileText size={17} aria-hidden />{view.published ? "阅读调研报告" : "查看报告草稿"}<ArrowUpRight size={16} aria-hidden /></Link>}
        </div>
      </header>
      {error && <div className="notice error-text"><p role="alert">{error}</p><button type="button" className="text-link" onClick={refresh}>重新读取状态</button></div>}
      {task.status === "failed" && view.error && <p className="notice error-text" role="alert">{view.error.message}</p>}
      <WorkspacePanes left={<section className="panel workspace-panel guild-panel" aria-labelledby="guild-nav-title">
        <header className="panel-heading">
          <span className="workspace-icon"><UsersRound size={18} aria-hidden /></span><h2 id="guild-nav-title">专家公会</h2>
          <Link className="workspace-icon sublink" href="/guild" aria-label="打开专家公会档案"><ArrowUpRight size={16} aria-hidden /></Link>
        </header>
        <div className="guild-scroll">
          <TeamGraph view={view} selected={agent} onSelect={setAgent} usageFor={research.usageFor} />
          {user && !example && view.assignments.some(({ agent_id, status }) =>
            agent_id === "travel_planning" && status === "completed")
            && <WorkbenchRoutePreview taskId={taskId} reportId={publishedId} />}
          <details className="workflow-details assignments-panel">
            <summary><ListChecks size={15} aria-hidden />任务分工
              {view.assignments.length > 0 && <span className="panel-aside">{completed}/{view.assignments.length} 已完成</span>}
            </summary>
            {view.plan ? <>
              <p className="plan-objective">{view.plan.objective}</p>
              <ul className="assignment-list">{view.assignments.map((assignment) => <li key={assignment.assignment_id}>
                <ExpertAvatar id={assignment.agent_id} />
                <div><ExpertLabel id={assignment.agent_id} /><p>{assignment.objective}</p>
                  {(assignment.depends_on ?? []).length > 0 && <small>依赖 {assignment.depends_on!.map((id) => {
                    const dependency = view.assignments.find((item) => item.assignment_id === id);
                    return dependency ? expertName(dependency.agent_id) : id;
                  }).join("、")}</small>}
                </div>
                <span className={`task-badge ${assignment.status}`}><span className={`status-dot ${assignment.status}`} />{assignmentLabels[assignment.status]}</span>
              </li>)}</ul>
            </> : <p className="plan-objective">等待调研总监发布研究计划。</p>}
          </details>
        </div>
      </section>}
        center={<EventFeed events={events} taskId={taskId} toolNames={view.toolNames} agent={agent} connection={connection}
          research={research} controls={controls} onSource={setSelected} />}
        right={<ResearchBrowser taskId={taskId} sources={research.sources} evidence={evidence} connection={example ? "closed" : live.researchConnection}
          selected={selected} onSelect={setSelected} citedIds={citedIds} publishedId={publishedId} onError={handleError} />} />
    </main>
  );
}
