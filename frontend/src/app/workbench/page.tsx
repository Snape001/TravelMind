import Link from "next/link";
import { TaskHistory } from "@/components/task-history";
import { demo } from "@/lib/demo";
import { workbenchHref } from "@/lib/research";

export const metadata = { title: "调研历史 · TravelMind" };

export default function WorkbenchIndex() {
  return <main className="shell inner-page narrow">
    <div className="page-heading">
      <h1>调研历史</h1><p>查看此浏览器中最近创建或打开的任务。</p>
    </div>
    <TaskHistory />
    <div className="history-actions">
      <Link className="button primary" href="/">新建调研</Link>
      <Link className="text-link" href={workbenchHref(demo.taskId)}>查看示例工作台</Link>
    </div>
  </main>;
}
