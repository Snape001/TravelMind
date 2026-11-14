import { uuidPattern } from "./research";

type StorageLike = Pick<Storage, "getItem" | "setItem">;
export const historyKey = (userId: string, mock: boolean) =>
  `travelmind.${mock ? "fixture" : "live"}.task-history.v1.${userId}`;

export function readTaskHistory(storage: Pick<Storage, "getItem">, key: string): string[] {
  try {
    const value: unknown = JSON.parse(storage.getItem(key) ?? "[]");
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter((id): id is string => typeof id === "string" && uuidPattern.test(id)))].slice(0, 20);
  } catch {
    return [];
  }
}

export function rememberTask(storage: StorageLike, key: string, taskId: string) {
  try {
    const previous = readTaskHistory(storage, key).filter((id) => id !== taskId);
    storage.setItem(key, JSON.stringify([taskId, ...previous].slice(0, 20)));
  } catch {
    // Direct task links remain usable with browser storage disabled.
  }
}
