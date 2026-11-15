"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, ArrowUpRight, FileText } from "lucide-react";
import type { ReportPage } from "@/lib/account";
import { demo } from "@/lib/demo";
import { formatDate, reportHref } from "@/lib/research";
import { missingEndpoint, readAccountReports } from "@/lib/task-client";
import { SessionPrompt, useSession } from "./session";

type PageState = { status: "loading" } | { status: "ready"; page: ReportPage } | { status: "error"; message: string };

export function ReportArchive() {
  const { session } = useSession();
  return <div className="report-archive">
    <section aria-labelledby="my-reports">
      <h2 id="my-reports">我的报告</h2>
      {session.user ? <ArchiveList key={session.user.user_id} /> :
        <SessionPrompt message="登录后可以查看已发布的调研报告。" />}
    </section>
    <section aria-labelledby="sample-reports">
      <h2 id="sample-reports">示例报告</h2>
      <ReportLink taskId={demo.taskId} reportId={demo.reportId} title={demo.title} detail={formatDate(demo.date)} />
    </section>
  </div>;
}

function ArchiveList() {
  const { handleError } = useSession();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PageState>({ status: "loading" });
  const pageNumber = cursors.length;
  const cursor = cursors[pageNumber - 1];

  useEffect(() => {
    const controller = new AbortController();
    readAccountReports(cursor, controller.signal).then((page) => {
      if (!controller.signal.aborted) setState({ status: "ready", page });
    }).catch((problem: unknown) => {
      if (controller.signal.aborted) return;
      handleError(problem);
      const message = missingEndpoint(problem) ? "报告列表暂不可用。仍可通过已有链接打开报告。" :
        problem instanceof Error ? problem.message : "暂时无法读取报告，请重试。";
      setState({ status: "error", message });
    });
    return () => controller.abort();
  }, [attempt, cursor, handleError]);

  function goTo(nextCursors: (string | null)[]) {
    setState({ status: "loading" });
    setCursors(nextCursors);
  }

  const page = state.status === "ready" ? state.page : null;
  const next = page?.next_cursor ?? null;
  return <>
    {state.status === "loading" && <p className="archive-status muted" role="status">正在读取报告…</p>}
    {state.status === "error" && <div className="archive-status">
      <p role="alert" className="error-text">{state.message}</p>
      <button type="button" className="text-link" onClick={() => {
        setState({ status: "loading" });
        setAttempt((value) => value + 1);
      }}>重新加载</button>
    </div>}
    {page && (page.items.length > 0 ? <ul className="report-list">
      {page.items.map((report) => <li key={report.report_id}>
        <ReportLink taskId={report.task_id} reportId={report.report_id} title={report.title}
          detail={`${formatDate(report.published_at)} · 第 ${report.version} 版`} />
      </li>)}
    </ul> : <div className="archive-status">
      <p className="muted">{pageNumber === 1 ? "还没有已发布的报告。" : "这一页没有报告。"}</p>
      {pageNumber === 1 && <Link className="text-link" href="/">开始一次调研 <ArrowUpRight size={15} aria-hidden /></Link>}
    </div>)}
    {(pageNumber > 1 || next !== null) && <nav className="archive-pages" aria-label="报告分页">
      {pageNumber > 1 && <button type="button" className="button secondary" onClick={() => goTo(cursors.slice(0, -1))}>
        <ArrowLeft size={16} aria-hidden />上一页
      </button>}
      <span role="status">第 {pageNumber} 页</span>
      {next !== null && !cursors.includes(next) &&
        <button type="button" className="button secondary" onClick={() => goTo([...cursors, next])}>
          下一页<ArrowRight size={16} aria-hidden />
        </button>}
    </nav>}
  </>;
}

function ReportLink({ taskId, reportId, title, detail }: { taskId: string; reportId: string; title: string; detail: string }) {
  return <Link className="report-row" href={reportHref(taskId, reportId)}>
    <span className="icon-tile"><FileText size={19} aria-hidden /></span>
    <span><strong>{title}</strong><small>{detail}</small></span>
    <ArrowUpRight size={18} aria-hidden />
  </Link>;
}
