import { uuidPattern } from "./research";

type StorageLike = Pick<Storage, "getItem" | "setItem">;
const historyKey = (userId: string) => `travelmind.live.task-history.v1.${userId}`;

export function readTaskHistory(userId: string, storage?: Pick<Storage, "getItem">): string[] {
  try {
    const value: unknown = JSON.parse((storage ?? localStorage).getItem(historyKey(userId)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return [...new Set(value.filter((id): id is string => typeof id === "string" && uuidPattern.test(id)))].slice(0, 20);
  } catch {
    return [];
  }
}

export function rememberTask(userId: string, taskId: string, storage?: StorageLike) {
  try {
    const previous = readTaskHistory(userId, storage).filter((id) => id !== taskId);
    (storage ?? localStorage).setItem(historyKey(userId), JSON.stringify([taskId, ...previous].slice(0, 20)));
  } catch {
    // Direct task links remain usable with browser storage disabled.
  }
}
