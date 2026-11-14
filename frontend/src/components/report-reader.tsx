"use client";

import Link from "next/link";
import { ArrowLeft, ArrowUpRight, FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { EvidenceCard } from "./evidence-card";
import { expertName, findExpert } from "@/lib/experts";
import { isMockTask, readReport } from "@/lib/task-client";
import { formatDate, type ReportRead } from "@/lib/research";
import { demo } from "@/lib/demo";
import { SessionPrompt, useSession } from "./session";

export function ReportReader({ taskId, reportId }: { taskId: string; reportId: string }) {
  const { session } = useSession();
  if (!session.user && taskId !== demo.taskId) return <main className="shell inner-page">
    <div className="page-heading"><h1>调研报告</h1></div><SessionPrompt />
  </main>;
  return <ReaderSession key={session.user?.user_id ?? "demo"} taskId={taskId} reportId={reportId} />;
}

function ReaderSession({ taskId, reportId }: { taskId: string; reportId: string }) {
  const { handleError } = useSession();
  const [report, setReport] = useState<ReportRead | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    readReport(taskId, reportId, controller.signal).then((data) => {
      if (!controller.signal.aborted) setReport(data);
    }).catch((problem: Error) => {
      if (!controller.signal.aborted) { handleError(problem); setError(problem.message); }
    });
    return () => controller.abort();
  }, [taskId, reportId, attempt, handleError]);

  useEffect(() => {
    if (report && location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [report]);

  const sections = report?.content.sections ?? [];
  const evidence = new Map(Array.from(new Set(sections.flatMap(({ evidence_ids }) => evidence_ids ?? [])))
    .map((id, index) => [id, index + 1]));
  return (
    <main className="shell inner-page">
      <div className="reader-navigation">
        <Link className="text-link back" href="/reports"><ArrowLeft size={16} aria-hidden /> 报告中心</Link>
        <Link className="text-link back" href={`/workbench/${taskId}`}>查看调研过程 <ArrowUpRight size={16} aria-hidden /></Link>
      </div>
      {report ? <>
        <header className="report-masthead">
          <p className="eyebrow"><FileText size={17} aria-hidden /> {isMockTask(taskId) ? "模拟调研报告" : "调研报告"}</p>
          <h1>{report.content.title}</h1>
          <p>第 {report.version} 版 · {formatDate(report.created_at)} · {sections.length} 个章节 · {evidence.size} 条引用</p>
        </header>
        <div className="reader-grid">
          <nav className="panel toc" aria-label="报告目录">
            <h2>目录</h2>
            <ol>{sections.map((section, index) => <li key={index}><a href={`#section-${index + 1}`}>{section.heading}</a></li>)}
              {evidence.size > 0 && <li><a href="#sources">参考资料与证据</a></li>}
            </ol>
          </nav>
          <article className="panel report-content">
            {sections.map((section, index) => <section key={index} id={`section-${index + 1}`} className="report-section">
              <h2>{section.heading}</h2>
              <div className="byline">章节署名 {section.author_agent_ids.map((id) =>
                findExpert(id) ? <Link key={id} href={`/guild/${id}`}>{expertName(id)}</Link> : <span key={id}>{expertName(id)}</span>)}</div>
              <p className="report-body">{section.body}</p>
              {(section.evidence_ids?.length ?? 0) > 0 && <p className="citation-links">引用 {Array.from(new Set(section.evidence_ids)).map((id) =>
                <a className="chip" key={id} href={`#evidence-${id}`} aria-label={`查看引用 ${evidence.get(id)}`}>[{evidence.get(id)}]</a>)}</p>}
            </section>)}
            {evidence.size > 0 && <section id="sources" className="report-section">
              <h2>参考资料与证据</h2>
              <ol className="bibliography">{Array.from(evidence).map(([id, index]) =>
                <li key={id} id={`evidence-${id}`}><span className="citation-index">[{index}]</span><EvidenceCard taskId={taskId} evidenceId={id} /></li>)}</ol>
            </section>}
          </article>
        </div>
      </> : <section className="panel empty-state">
        {error ? <><p className="error-text" role="alert">{error}</p><button type="button" className="button secondary" onClick={() => {
          setError(""); setAttempt((value) => value + 1);
        }}>重新加载</button></> : <p role="status">正在读取报告…</p>}
      </section>}
    </main>
  );
}
