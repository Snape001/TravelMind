"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUpRight, FlaskConical } from "lucide-react";
import { ApiError, readTask } from "@/lib/task-client";
import { destinationLoginDraft } from "@/lib/intake";
import { readTaskHistory } from "@/lib/task-history";
import { formatDate, taskStatusLabels, workbenchHref, type TaskRead } from "@/lib/research";
import { SessionPrompt, useSession } from "./session";

export function TaskHistory() {
  const { session } = useSession();
  const router = useRouter();
  const userId = session.user?.user_id;
  useEffect(() => {
    if (!userId) return;
    try {
      if (sessionStorage.getItem(destinationLoginDraft)) router.replace("/");
    } catch { /* History remains usable without browser storage. */ }
  }, [userId, router]);
  return session.user ? <HistoryList key={session.user.user_id} userId={session.user.user_id} /> : <SessionPrompt />;
}

function HistoryList({ userId }: { userId: string }) {
  const { handleError } = useSession();
  const [tasks, setTasks] = useState<TaskRead[] | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const ids = readTaskHistory(userId);
    Promise.allSettled(ids.map((id) => readTask(id, controller.signal))).then((results) => {
      if (controller.signal.aborted) return;
      const failures = results.flatMap((result) => result.status === "rejected" ? [result.reason as unknown] : []);
      failures.forEach(handleError);
      setError(failures.some((failure) => !(failure instanceof ApiError && failure.status === 404)));
      setTasks(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []));
    });
    return () => controller.abort();
  }, [userId, handleError, attempt]);

  return <>
    {tasks === null && <p role="status" className="muted">正在读取历史任务…</p>}
    {error && <div className="notice error-text"><p role="alert">部分任务暂时无法读取。</p>
      <button className="text-link" type="button" onClick={() => setAttempt((value) => value + 1)}>重试</button></div>}
    {tasks?.length === 0 && !error && <p className="panel empty-state">此浏览器暂无可访问的历史任务。</p>}
    {!!tasks?.length && <ul className="report-list">{tasks.map((task) => <li key={task.task_id}>
      <Link className="report-row task-history-row" href={workbenchHref(task.task_id)}>
        <span className="icon-tile"><FlaskConical size={19} aria-hidden /></span>
        <span><strong>{task.brief.destination}调研</strong><small>更新于 {formatDate(task.updated_at)}</small></span>
        <span className={`task-badge ${task.status}`}>{task.status === "running" && task.pause_requested_at ? "等待暂停" : taskStatusLabels[task.status]}</span>
        <ArrowUpRight size={18} aria-hidden />
      </Link>
    </li>)}</ul>}
  </>;
}
