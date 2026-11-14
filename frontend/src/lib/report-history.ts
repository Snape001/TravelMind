import { mockMode } from "./task-client";

type ReportKey = { taskId: string; reportId: string };
export type ArchiveEntry = ReportKey & { title: string; date: string };
const archiveKey = (userId: string) => `travelmind.${mockMode ? "fixture" : "live"}.reports.v2.${userId}`;

export function readArchive(userId: string): ArchiveEntry[] {
  try {
    const contents = JSON.parse(localStorage.getItem(archiveKey(userId)) ?? "[]");
    if (!Array.isArray(contents)) return [];
    return contents.filter((entry): entry is ArchiveEntry => entry && typeof entry.taskId === "string" &&
      typeof entry.reportId === "string" && typeof entry.title === "string" &&
      typeof entry.date === "string" && Number.isFinite(Date.parse(entry.date)));
  } catch {
    return [];
  }
}

export function rememberReport(userId: string, entry: ArchiveEntry) {
  try {
    localStorage.setItem(archiveKey(userId), JSON.stringify([entry, ...readArchive(userId).filter(({ taskId }) => taskId !== entry.taskId)].slice(0, 20)));
  } catch {
    // The report remains readable from its link when browser storage is unavailable.
  }
}
