"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { demo, lastTaskKey } from "@/lib/demo";

export default function LatestWorkbench() {
  const router = useRouter();
  useEffect(() => {
    let id: string | null = null;
    try { id = sessionStorage.getItem(lastTaskKey); } catch { /* Use the example workbench when storage is unavailable. */ }
    router.replace(`/workbench/${encodeURIComponent(id || demo.taskId)}`);
  }, [router]);

  return <main className="shell inner-page">
    <div className="page-heading"><h1>调研工作台</h1></div>
    <p role="status" className="muted">正在打开工作台…</p>
    <Link className="text-link" href={`/workbench/${demo.taskId}`}>查看示例工作台</Link>
  </main>;
}
