const loginReturnKey = "travelmind.login-return";
const loginReturnLifetime = 10 * 60 * 1000;

type ReturnStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function rememberLoginReturn(location: Pick<Location, "pathname" | "search" | "hash">,
  storage: ReturnStorage, now = Date.now()) {
  const path = location.pathname + location.search + location.hash;
  storage.setItem(loginReturnKey, JSON.stringify({ path, expiresAt: now + loginReturnLifetime }));
}

export function safeLoginReturn(path: string, origin: string): string | null {
  if (!path.startsWith("/") || path.startsWith("//") || /[\s\\]/u.test(path)) return null;
  try {
    const { origin: targetOrigin, pathname, search, hash } = new URL(path, origin);
    const decoded = decodeURIComponent(pathname);
    if (targetOrigin !== origin || decoded.startsWith("//") || decoded.includes("\\") ||
      /^\/(api|_next|_amapservice)(?:\/|$)/iu.test(decoded)) return null;
    return pathname + search + hash;
  } catch { return null; }
}

export function takeLoginReturn(origin: string, storage: ReturnStorage, now = Date.now()): string | null {
  const saved = storage.getItem(loginReturnKey);
  if (!saved) return null;
  storage.removeItem(loginReturnKey);
  try {
    const record = JSON.parse(saved);
    if (!Number.isFinite(record?.expiresAt) || record.expiresAt <= now || typeof record.path !== "string") return null;
    return safeLoginReturn(record.path, origin);
  } catch { return null; }
}
