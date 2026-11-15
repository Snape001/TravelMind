import { ReportArchive } from "@/components/report-archive";

export const metadata = { title: "报告中心 · TravelMind" };

export default function Reports() {
  return (
    <main className="shell inner-page narrow account-page">
      <div className="page-heading"><h1>报告中心</h1></div>
      <ReportArchive />
    </main>
  );
}
