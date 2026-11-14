import { ReportArchive } from "@/components/report-archive";

export const metadata = { title: "报告中心 · TravelMind" };

export default function Reports() {
  return (
    <main className="shell inner-page narrow">
      <div className="page-heading"><h1>报告中心</h1><p>查看示例报告与此浏览器保存的调研报告记录。</p></div>
      <ReportArchive />
    </main>
  );
}
