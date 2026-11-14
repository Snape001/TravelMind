import { fixtureCallback, fixtureError, fixtureLogin, fixtureLogout, fixtureSession } from "@/lib/fixture-session";

type Context = { params: Promise<{ action: string }> };
const missing = () => fixtureError(404, "NOT_FOUND", "页面不存在。");

export async function GET(request: Request, { params }: Context) {
  if (process.env.NEXT_PUBLIC_RESEARCH_MODE !== "mock") return missing();
  const { action } = await params;
  if (action === "login") return fixtureLogin();
  if (action === "callback") return fixtureCallback();
  if (action !== "me") return missing();
  const user = fixtureSession(request);
  return user ? Response.json(user, { headers: { "Cache-Control": "no-store" } }) :
    fixtureError(401, "UNAUTHENTICATED", "请先登录。");
}

export async function POST(request: Request, { params }: Context) {
  if (process.env.NEXT_PUBLIC_RESEARCH_MODE !== "mock" || (await params).action !== "logout") return missing();
  return fixtureLogout(request);
}
