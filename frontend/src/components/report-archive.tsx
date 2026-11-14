"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, FileText } from "lucide-react";
import { demo } from "@/lib/demo";
import { formatDate, reportHref } from "@/lib/research";
import { readArchive, type ArchiveEntry } from "@/lib/report-history";
import { isMockTask } from "@/lib/task-client";

const example: ArchiveEntry = { taskId: demo.taskId, reportId: demo.reportId, title: demo.title, date: demo.date };

export function ReportArchive({ compact = false }: { compact?: boolean }) {
  const [reports, setReports] = useState<ArchiveEntry[]>([]);
  useEffect(() => {
    function update() { setReports(readArchive()); }
    update();
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  const items = [...reports.filter(({ taskId }) => taskId !== demo.taskId), example];
  return (
    <ul className="report-list">
      {(compact ? items.slice(0, 3) : items).map(({ taskId, reportId, title, date }) => (
        <li key={taskId}>
          <Link className="report-row" href={reportHref(taskId, reportId)}>
            <span className="icon-tile"><FileText size={19} aria-hidden /></span>
            <span><strong>{title}</strong><small>{formatDate(date)} · {taskId === demo.taskId ? "示例报告" : isMockTask(taskId) ? "模拟报告" : "调研报告"}</small></span>
            <ArrowUpRight size={18} aria-hidden />
          </Link>
        </li>
      ))}
    </ul>
  );
}
