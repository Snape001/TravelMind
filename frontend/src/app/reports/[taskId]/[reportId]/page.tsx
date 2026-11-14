import { ReportReader } from "@/components/report-reader";

export const metadata = { title: "调研报告 · TravelMind" };

export default async function ReportPage({ params }: { params: Promise<{ taskId: string; reportId: string }> }) {
  const { taskId, reportId } = await params;
  return <ReportReader key={`${taskId}/${reportId}`} taskId={taskId} reportId={reportId} />;
}
