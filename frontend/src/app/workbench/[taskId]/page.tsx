import { Workbench } from "@/components/workbench";

export const metadata = { title: "调研工作台 · TravelMind" };

export default async function WorkbenchPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  return <Workbench key={taskId} taskId={taskId} />;
}
