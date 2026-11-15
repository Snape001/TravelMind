import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { providerNavigation, type AccountUser, type ReportPage } from "../src/lib/account";
import { safeLoginReturn } from "../src/lib/login-return";
import { reportHref } from "../src/lib/research";
import { ApiError, editProfile, logoutAccount, readAccountReports, readSession } from "../src/lib/task-client";

const fixture = JSON.parse(readFileSync("../fixtures/account-auth.json", "utf8")) as {
  session: AccountUser;
  legacySession: AccountUser;
  reports: ReportPage;
  lastReports: ReportPage;
  edit: { authorization_url: string };
  logout: { provider_logout_url: string };
  partialLogout: { provider_logout_url: null };
  errors: Record<string, { detail: { code: string; message: string; retryable: boolean } }>;
};

test("my reports keeps the server cursor opaque and links to the exact published report", async (t) => {
  const controller = new AbortController();
  const responses = [fixture.reports, fixture.lastReports];
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, options: RequestInit) => {
    calls.push(url);
    assert.equal(options.cache, "no-store");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error");
    assert.equal(options.signal, controller.signal);
    return Response.json(responses[calls.length - 1]);
  });
  const first = await readAccountReports(null, controller.signal);
  const last = await readAccountReports(first.next_cursor, controller.signal);
  assert.deepEqual([first, last], responses);
  assert.equal(calls[0], "/api/v1/account/reports?limit=20");
  assert.equal(new URL(calls[1], "https://travelmind.example").searchParams.get("cursor"), first.next_cursor);
  const report = last.items[0];
  assert.equal(reportHref(report.task_id, report.report_id), `/reports/${report.task_id}/${report.report_id}`);
});

test("profile edit and capability-aware logout use same-origin CSRF with no request body", async (t) => {
  const calls: string[] = [];
  const responses = [Response.json(fixture.edit), Response.json(fixture.logout),
    Response.json(fixture.partialLogout), new Response(null, { status: 204 })];
  t.mock.method(globalThis, "fetch", async (url: string, options: RequestInit) => {
    calls.push(url);
    assert.equal(options.method, "POST");
    assert.equal(options.body, undefined);
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error");
    assert.equal(new Headers(options.headers).get("X-CSRF-Token"), fixture.session.csrf_token);
    return responses[calls.length - 1];
  });
  assert.deepEqual(await editProfile(fixture.session.csrf_token), fixture.edit);
  assert.equal(await logoutAccount(fixture.session), fixture.logout.provider_logout_url);
  assert.equal(await logoutAccount(fixture.session), null);
  assert.equal(await logoutAccount(fixture.legacySession), null);
  assert.deepEqual(calls, ["/api/v1/auth/profile/edit", "/api/v1/auth/logout/oidc",
    "/api/v1/auth/logout/oidc", "/api/v1/auth/logout"]);
});

test("account clients surface expired, unverified, disabled, unavailable and invalid-cursor errors", async (t) => {
  const failures = [
    [401, fixture.errors.expired, () => readAccountReports(null)],
    [403, fixture.errors.unverified, () => readSession()],
    [404, fixture.errors.disabled, () => editProfile(fixture.session.csrf_token)],
    [503, fixture.errors.unavailable, () => logoutAccount(fixture.session)],
    [422, fixture.errors.invalidCursor, () => readAccountReports("invalid")],
  ] as const;
  let index = 0;
  t.mock.method(globalThis, "fetch", async () => {
    const [status, body] = failures[index++];
    return Response.json(body, { status });
  });
  for (const [status, body, request] of failures) {
    await assert.rejects(request, (problem: unknown) => problem instanceof ApiError &&
      problem.status === status && problem.code === body.detail.code && problem.retryable === body.detail.retryable);
  }
});

test("login return and server-issued provider links are validated for their distinct destinations", () => {
  const origin = "https://travelmind.example";
  const path = "/reports/123/456?view=full#section-2";
  assert.equal(safeLoginReturn(path, origin), path);
  for (const unsafe of ["//example.test", "/\\example.test", "/%2fexample.test", "/%5cexample.test",
    "/%61pi/v1/auth/login", "/workbench/../api/v1/auth/logout", "/_next/data", "https://travelmind.example/"]) {
    assert.equal(safeLoginReturn(unsafe, origin), null, unsafe);
  }
  assert.equal(providerNavigation(fixture.edit.authorization_url), fixture.edit.authorization_url);
  assert.equal(providerNavigation(fixture.logout.provider_logout_url), fixture.logout.provider_logout_url);
  for (const url of ["javascript:alert(1)", "data:text/html,html", "//id.example.test", "/account",
    "http://id.example.test", "https://id.example.test@elsewhere.test", "https://id.example.test/\\bad",
    "https://id.example.test/#action"]) assert.throws(() => providerNavigation(url));
});
