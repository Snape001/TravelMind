import type { CurrentUser } from "../../src/lib/research";

const cookie = "travelmind_fixture_session";
const sessions = new Map<string, CurrentUser>();
const identity = { user_id: "4fb7f515-2097-4a09-9fde-d5de25d2143d", display_name: "契约测试用户" };

export function fixtureSession(request: Request) {
  const token = request.headers.get("cookie")?.split(";").map((value) => value.trim())
    .find((value) => value.startsWith(`${cookie}=`))?.slice(cookie.length + 1);
  return token ? sessions.get(token) : undefined;
}

function fixtureError(status: number, code: string, message: string) {
  return Response.json({ detail: { code, message, retryable: false } }, { status });
}

export function checkFixtureWrite(request: Request, user: CurrentUser) {
  const { protocol, host } = new URL(request.url);
  const origin = `${protocol}//${request.headers.get("Host") ?? host}`;
  return request.headers.get("Origin") === origin && request.headers.get("X-CSRF-Token") === user.csrf_token;
}

const sessionCookie = (value: string, age: number) => `${cookie}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${age}`;

export function fixtureCallback() {
  const salt = crypto.randomUUID();
  sessions.set(salt, { ...identity, csrf_token: crypto.randomUUID() });
  return new Response(null, { status: 302, headers: { Location: "/workbench", "Set-Cookie": sessionCookie(salt, 3600), "Cache-Control": "no-store" } });
}

export function fixtureLogout(request: Request) {
  const user = fixtureSession(request);
  if (!user) return fixtureError(401, "UNAUTHENTICATED", "请先登录。");
  if (!checkFixtureWrite(request, user)) return fixtureError(403, "CSRF_INVALID", "会话校验失败，请重试。");
  for (const [token, current] of sessions) {
    if (current === user) sessions.delete(token);
  }
  return new Response(null, { status: 204, headers: { "Set-Cookie": sessionCookie("", 0), "Cache-Control": "no-store" } });
}
