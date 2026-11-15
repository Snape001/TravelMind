const loginReturnKey = "travelmind.login-return";
const loginReturnLifetime = 10 * 60 * 1000;

type ReturnStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function rememberLoginReturn(location: Pick<Location, "pathname" | "search" | "hash">,
  storage: ReturnStorage, now = Date.now()) {
  const path = location.pathname + location.search + location.hash;
  storage.setItem(loginReturnKey, JSON.stringify({ path, expiresAt: now + loginReturnLifetime }));
}

export function takeLoginReturn(origin: string, storage: ReturnStorage, now = Date.now()): string | null {
  const saved = storage.getItem(loginReturnKey);
  if (!saved) return null;
  storage.removeItem(loginReturnKey);
  try {
    const record = JSON.parse(saved);
    if (typeof record?.expiresAt !== "number" || record.expiresAt <= now || typeof record.path !== "string" ||
      !record.path.startsWith("/")) return null;
    const { origin: targetOrigin, pathname, search, hash } = new URL(record.path, origin);
    if (targetOrigin !== origin || pathname.startsWith("//") || pathname === "/api" || pathname.startsWith("/api/")) return null;
    return pathname + search + hash;
  } catch { return null; }
}
