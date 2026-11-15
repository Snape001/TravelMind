import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, update

from travelmind.api import tasks
from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.api.oidc import AuthSettings, OidcClient
from travelmind.db.auth import csrf_token, revoke_session, start_session, token_hash
from travelmind.db.models import SessionRow, TaskRow
from travelmind.schemas.worker import ProjectionBatch

pytestmark = pytest.mark.postgres
ISSUER = "https://id.example"
CLIENT_ID = "travelmind-tests"


def test_oidc_login_and_revocation(client):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    app = client.app
    origin = str(client.base_url).rstrip("/")
    settings = AuthSettings(
        _env_file=None,
        issuer=ISSUER,
        public_origin=origin,
        client_id=CLIENT_ID,
        token_auth_method="none",
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update(kid="key-1", use="sig", alg="RS256")
    attempt = {}
    faults = {
        "bad_nonce": {"nonce": "different"},
        "bad_issuer": {"iss": "https://other.example"},
        "bad_audience": {"aud": "unexpected-client"},
        "expired": {"exp": datetime.now(UTC) - timedelta(hours=1)},
    }
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def provider(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        assert request.url.path == "/token"
        form = parse_qs(request.content.decode())
        assert form["client_id"] == [CLIENT_ID]
        assert form["redirect_uri"] == [f"{origin}/api/v1/auth/callback"]
        verifier = form["code_verifier"][0].encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).decode().rstrip("=")
        assert challenge == attempt["code_challenge"][0]
        token_type = form["code"][0]
        now = datetime.now(UTC)
        claims = {
            "iss": ISSUER,
            "sub": "test-user",
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "nonce": attempt["nonce"][0],
            "name": "测试用户",
            **faults.get(token_type, {}),
        }
        return httpx.Response(
            200,
            json={
                "id_token": jwt.encode(
                    claims,
                    wrong_key if token_type == "bad_signature" else private_key,
                    algorithm="RS256",
                    headers={"kid": "key-1"},
                )
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app.state.oidc = OidcClient(upstream, settings)
    try:
        browser = client
        browser.cookies.clear()
        browser.follow_redirects = False

        def begin():
            login = browser.get("/api/v1/auth/login")
            assert login.status_code == 302
            assert {"httponly", "secure", "samesite=lax"} <= {
                part.strip().lower() for part in login.headers["set-cookie"].split(";")
            }
            attempt.update(parse_qs(urlsplit(login.headers["location"]).query))
            assert attempt["code_challenge_method"] == ["S256"]
            return attempt["state"][0]

        callback = "/api/v1/auth/callback"
        for failure in (*faults, "bad_signature"):
            response = browser.get(callback, params={"state": begin(), "code": failure})
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "AUTH_CALLBACK_INVALID"

        state = begin()
        response = browser.get(callback, params={"state": "invalid", "code": "real"})
        assert response.status_code == 400
        response = browser.get(callback, params={"state": state, "code": "real"})
        assert response.status_code == 302
        assert response.headers["location"] == f"{origin}/workbench"
        session_cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(SESSION_COOKIE)
        )
        assert "Secure" in session_cookie and "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie
        me = browser.get("/api/v1/auth/me")
        assert me.status_code == 200
        user = me.json()
        assert user["display_name"] == "测试用户"
        old_session = browser.cookies[SESSION_COOKIE]
        assert browser.get(callback, params={"state": state, "code": "real"}).status_code == 400

        state = begin()
        assert browser.get(callback, params={"state": state, "code": "real"}).status_code == 302
        current = browser.get("/api/v1/auth/me").json()
        assert current["user_id"] == user["user_id"]
        assert current["csrf_token"] != user["csrf_token"]
        assert browser.post("/api/v1/auth/logout").status_code == 403
        response = browser.post(
            "/api/v1/auth/logout",
            headers={
                "Origin": origin,
                "X-CSRF-Token": current["csrf_token"],
            },
        )
        assert response.status_code == 204 and "Max-Age=0" in response.headers["set-cookie"]
        browser.cookies.set(SESSION_COOKIE, old_session)
        assert browser.get("/api/v1/auth/me").status_code == 401
    finally:
        client.portal.call(upstream.aclose)


def test_auth_and_resource_ownership_apply_to_every_task_route(client):
    base_url = str(client.base_url).rstrip("/")
    created = client.post(
        "/api/v1/tasks", json={"destination": "泉州"}, headers={"Idempotency-Key": "same-key"}
    )
    assert created.status_code == 201
    path = created.headers["location"]
    for suffix in ("", "/events", f"/reports/{uuid4()}", f"/evidence/{uuid4()}"):
        assert client.get(path + suffix, headers={"Cookie": ""}).status_code == 401
    assert (
        client.post(path + "/pause", headers={"Origin": "https://other.example"}).status_code == 403
    )
    assert client.post(path + "/pause", headers={"X-CSRF-Token": "other"}).status_code == 403

    missing = f"/api/v1/tasks/{uuid4()}"
    for missing_path, code in (
        (missing, "TASK_NOT_FOUND"),
        (f"{missing}/events", "TASK_NOT_FOUND"),
        (f"{path}/reports/{uuid4()}", "REPORT_NOT_FOUND"),
        (f"{path}/evidence/{uuid4()}", "EVIDENCE_NOT_FOUND"),
    ):
        response = client.get(missing_path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == code

    async def other_session():
        async with client.app.state.sessions.begin() as session:
            return await start_session(
                session, ISSUER, str(uuid4()), None, lifetime=3600, old_token=None
            )

    token = client.portal.call(other_session)
    headers = {
        "Cookie": f"{SESSION_COOKIE}={token}",
        "Origin": base_url,
        "X-CSRF-Token": csrf_token(token),
    }
    for suffix in ("", "/events", f"/reports/{uuid4()}", f"/evidence/{uuid4()}"):
        reply = client.get(path + suffix, headers=headers)
        assert reply.status_code == 404
        assert reply.json()["detail"]["code"] == "TASK_NOT_FOUND"
    reply = client.post(path + "/pause", headers={**headers, "Idempotency-Key": "same-key"})
    assert reply.status_code == 404
    separate = client.post(
        "/api/v1/tasks",
        json={"destination": "厦门"},
        headers={**headers, "Idempotency-Key": "same-key"},
    )
    assert separate.status_code == 201
    assert separate.json()["task_id"] != created.json()["task_id"]

    async def expire():
        async with client.app.state.sessions.begin() as session:
            user_id = await session.scalar(
                select(SessionRow.user_id).where(SessionRow.token_hash == token_hash(token))
            )
            await session.execute(
                update(TaskRow).where(TaskRow.owner_id == user_id).values(available_at=None)
            )
            await session.execute(
                update(SessionRow)
                .where(SessionRow.token_hash == token_hash(token))
                .values(expires_at=func.now())
            )

    client.portal.call(expire)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_sse_replays_from_exclusive_header_and_closes_terminal_task(client):
    from travelmind.db.worker import PostgresWorkerStore

    task = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert task.status_code == 201

    async def run():
        store = PostgresWorkerStore(client.app.state.sessions)
        claim = await store.claim(uuid4(), lease_seconds=30)
        assert str(claim.lease.task_id) == task.json()["task_id"]
        await store.commit_projection(
            claim.lease,
            ProjectionBatch(checkpoint_id="done", projection_index=1, transition="complete"),
        )

    client.portal.call(run)
    assert client.get(task.headers["location"] + "/events?after=-1").status_code == 422
    stream = client.get(
        task.headers["location"] + "/events?after=1",
        headers={"Last-Event-ID": "2"},
    )
    assert stream.status_code == 200
    assert "id: 3" in stream.text and "id: 2" not in stream.text
    assert '"status":"completed"' in stream.text


def test_open_sse_ends_after_session_is_revoked(client, monkeypatch):
    monkeypatch.setattr(tasks, "SSE_SESSION_CHECK_SECONDS", 0)
    created = client.post("/api/v1/tasks", json={"destination": "泉州"})
    assert created.status_code == 201
    token = client.cookies[SESSION_COOKIE]
    check = tasks.session_is_active
    polls = []

    async def watch(*args):
        polls.append(await check(*args))
        if len(polls) == 1:
            async with client.app.state.sessions.begin() as session:
                await revoke_session(session, token_hash(token))
        return polls[-1]

    monkeypatch.setattr(tasks, "session_is_active", watch)

    async def replay():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url=str(client.base_url),
            cookies={SESSION_COOKIE: token},
        ) as browser:
            return await asyncio.wait_for(browser.get(created.headers["location"] + "/events"), 4)

    response = client.portal.call(replay)
    assert response.status_code == 200
    assert "id: 1" in response.text
    assert polls == [True, False]
