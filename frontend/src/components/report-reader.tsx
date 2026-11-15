"use client";

import Link from "next/link";
import { ArrowLeft, ArrowUpRight, CircleCheck, FileText, MapPin, MessagesSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EvidenceCard } from "./evidence-card";
import { ExpertLabel } from "./expert-identity";
import { reportDiscussion } from "@/lib/report-discussion";
import { canonicalAgentId } from "@/lib/experts";
import { readReport } from "@/lib/task-client";
import { formatDate, workbenchHref, type ReportRead } from "@/lib/research";
import { demo, demoResearch } from "@/lib/demo";
import { splitPoiMentions } from "@/lib/report-map";
import { SessionPrompt, useSession } from "./session";
import { RouteMapPanel, useTaskRouteMap, type ReportChapter } from "./route-map-panel";
import type { PoiSelection } from "./route-map-canvas";

export function ReportReader({ taskId, reportId }: { taskId: string; reportId: string }) {
  const { session } = useSession();
  if (!session.user && taskId !== demo.taskId) return <main className="shell inner-page">
    <div className="page-heading"><h1>调研报告</h1></div><SessionPrompt />
  </main>;
  return <ReaderSession key={`${taskId}:${reportId}:${session.user?.user_id ?? "demo"}`} taskId={taskId} reportId={reportId} />;
}

function ReaderSession({ taskId, reportId }: { taskId: string; reportId: string }) {
  const { handleError } = useSession();
  const example = taskId === demo.taskId;
  const [loaded, setReport] = useState<ReportRead | null>(null);
  const [loadError, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const report = example ? demoResearch.reports.find(({ report_id }) => report_id === reportId) : loaded;
  const error = example && !report ? "该任务的报告暂不可用。" : loadError;
  const { state: routeState, retry: retryRoutes } = useTaskRouteMap(taskId, !!report && !example);
  const [mapSelection, setMapSelection] = useState<PoiSelection | null>(null);
  const selectPoi = useCallback((id: string) => setMapSelection({ id }), []);
  useEffect(() => {
    if (example) return;
    const controller = new AbortController();
    readReport(taskId, reportId, controller.signal).then((data) => {
      if (!controller.signal.aborted) {
        setReport(data);
        setError("");
      }
    }).catch((problem: Error) => {
      if (!controller.signal.aborted) { handleError(problem); setError(problem.message); }
    });
    return () => controller.abort();
  }, [taskId, reportId, attempt, handleError, example]);

  useEffect(() => {
    if (report && location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [report]);

  const sections = report?.content.sections ?? [];
  const routeData = routeState.kind === "ready" ? routeState.data : undefined;
  const linked = useMemo(() => {
    const stops = routeData?.stops.map(({ place }) => ({ id: place.poi_id, name: place.name })) ?? [];
    const chapters = new Map<string, ReportChapter[]>();
    const chaptersWithLinks = report?.content.sections.map((section, index) => {
      const heading = splitPoiMentions(section.heading, stops);
      const body = splitPoiMentions(section.body, stops);
      const ids = new Set([...heading, ...body].flatMap(({ id }) => id ? [id] : []));
      for (const id of ids) {
        const chapter = { id: `section-${index + 1}`, title: section.heading };
        const references = chapters.get(id);
        if (references) references.push(chapter);
        else chapters.set(id, [chapter]);
      }
      return { heading, body };
    });
    return { chapters, chaptersWithLinks };
  }, [report, routeData]);
  const evidence = new Map(Array.from(new Set(sections.flatMap(({ evidence_ids }) => evidence_ids ?? [])))
    .map((id, index) => [id, index + 1]));
  return (
    <main className="shell inner-page report-page">
      <div className="reader-navigation">
        <Link className="text-link back" href="/reports"><ArrowLeft size={16} aria-hidden /> 报告中心</Link>
        <Link className="text-link back" href={workbenchHref(taskId)}>查看调研过程 <ArrowUpRight size={16} aria-hidden /></Link>
      </div>
      {report ? <>
        <header className="report-masthead">
          <p className="eyebrow"><FileText size={17} aria-hidden /> {example ? "示例调研报告" : "调研报告"}</p>
          <h1>{report.content.title}</h1>
          <p>第 {report.version} 版 · {formatDate(report.created_at)} · {sections.length} 个章节 · {evidence.size} 条引用</p>
        </header>
        <div className="reader-grid">
          <nav className="panel toc" aria-label="报告目录">
            <h2>目录</h2>
            <ol><li><a href="#report-map">目的地地图与游览规划</a></li>
              {sections.map((section, index) => <li key={index}><a href={`#section-${index + 1}`}>{section.heading}</a></li>)}
              {evidence.size > 0 && <li><a href="#sources">参考资料与证据</a></li>}
            </ol>
          </nav>
          <article className="panel report-content">
            <section id="report-map" className="report-section" aria-labelledby="report-map-title">
              <h2 id="report-map-title" className="report-map-title"><MapPin size={21} aria-hidden />目的地地图与游览规划</h2>
              <RouteMapPanel state={routeState} selection={mapSelection} onSelect={selectPoi}
                retry={retryRoutes} chapters={linked.chapters} />
            </section>
            {sections.map((section, index) => {
              const discussion = reportDiscussion(section.heading);
              const DiscussionIcon = discussion === "consensus" ? CircleCheck : MessagesSquare;
              return <section key={index} id={`section-${index + 1}`} className="report-section">
                <div className={discussion ? `report-discussion ${discussion}` : undefined}>
                  <h2>{discussion && <DiscussionIcon size={21} aria-hidden />}
                    <PoiMentions parts={linked.chaptersWithLinks?.[index]?.heading} onSelect={selectPoi} />
                  </h2>
                  <div className="byline">
                    <span>章节署名</span>
                    <ul aria-label="本章专家">{Array.from(new Set(section.author_agent_ids.map(canonicalAgentId))).map((id) =>
                      <li key={id}><ExpertLabel id={id} avatar /></li>)}</ul>
                  </div>
                  <p className="report-body"><PoiMentions parts={linked.chaptersWithLinks?.[index]?.body} onSelect={selectPoi} /></p>
                  {(section.evidence_ids?.length ?? 0) > 0 && <p className="citation-links">引用 {Array.from(new Set(section.evidence_ids)).map((id) =>
                    <a className="chip" key={id} href={`#evidence-${id}`} aria-label={`查看引用 ${evidence.get(id)}`}>[{evidence.get(id)}]</a>)}</p>}
                </div>
              </section>;
            })}
            {evidence.size > 0 && <section id="sources" className="report-section">
              <h2>参考资料与证据</h2>
              <ol className="bibliography">{Array.from(evidence).map(([id, index]) =>
                <li key={id} id={`evidence-${id}`}><span className="citation-index">[{index}]</span><EvidenceCard taskId={taskId} evidenceId={id} /></li>)}</ol>
            </section>}
          </article>
        </div>
      </> : <section className="panel empty-state">
        {error ? <><p className="error-text" role="alert">{error}</p>{!example && <button type="button" className="button secondary" onClick={() => {
          setError(""); setAttempt((value) => value + 1);
        }}>重新加载</button>}</> : <p role="status">正在读取报告…</p>}
      </section>}
    </main>
  );
}

function PoiMentions({ parts, onSelect }: { parts?: ReturnType<typeof splitPoiMentions>; onSelect: (id: string) => void }) {
  return parts?.map(({ text, id }, index) => id
    ? <a className="report-poi-link" key={index} href="#report-map" onClick={() => onSelect(id)}>{text}</a>
    : text);
}
