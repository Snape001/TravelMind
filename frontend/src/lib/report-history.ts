type ReportKey = { taskId: string; reportId: string };
export type ArchiveEntry = ReportKey & { title: string; date: string };
const key = "travelmind.mock-reports.v1";

export function readArchive(): ArchiveEntry[] {
  try {
    const contents = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (!Array.isArray(contents)) return [];
    return contents.filter((entry): entry is ArchiveEntry => entry && typeof entry.taskId === "string" &&
      typeof entry.reportId === "string" && typeof entry.title === "string" &&
      typeof entry.date === "string" && Number.isFinite(Date.parse(entry.date)));
  } catch {
    return [];
  }
}

export function rememberReport(entry: ArchiveEntry) {
  try {
    localStorage.setItem(key, JSON.stringify([entry, ...readArchive().filter(({ taskId }) => taskId !== entry.taskId)].slice(0, 20)));
  } catch {
    // The report remains readable from its link when browser storage is unavailable.
  }
}
