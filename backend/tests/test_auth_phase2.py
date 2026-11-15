import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from travelmind.api import tasks
from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.api.oidc import LOGOUT_EVENT, AuthSettings, OidcClient
from travelmind.db.auth import csrf_token, revoke_session, start_session, token_hash
from travelmind.db.models import LogoutReceiptRow, SessionRow, TaskRow, UserRow
from travelmind.schemas.worker import ProjectionBatch

pytestmark = pytest.mark.postgres
ISSUER = "https://id.example"
CLIENT_ID = "travelmind-tests"


def test_oidc_login_and_revocation(client):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    app = client.app
    origin = str(client.base_url).rstrip("/")
    subject = str(uuid4())
    oidc_sid = str(uuid4())
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
        "unverified": {"email_verified": False},
        "fake_boolean": {"email_verified": "true"},
        "missing_sid": {"sid": None},
        "other_user": {"sub": "another-user"},
        "renamed": {"name": "更新姓名", "email": "renamed@example.test"},
    }
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    advertise_logout = True

    def provider(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    **({"end_session_endpoint": f"{ISSUER}/logout"} if advertise_logout else {}),
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
            "sub": subject,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "nonce": attempt["nonce"][0],
            "name": "测试用户",
            "email": "account@example.test",
            "email_verified": True,
            "picture": "https://id.example/avatar.png",
            "sid": oidc_sid,
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
        disabled = browser.get("/api/v1/auth/register")
        assert disabled.status_code == 404
        assert disabled.json()["detail"]["code"] == "ACCOUNT_FEATURE_DISABLED"

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
        for failure in ("bad_nonce", "bad_issuer", "bad_audience", "expired", "bad_signature"):
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
        assert user["account_management_enabled"] is False
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

        app.state.auth_settings = settings.model_copy(update={"account_enabled": True})
        app.state.oidc.settings = app.state.auth_settings
        for failure in ("unverified", "fake_boolean"):
            reply = browser.get(callback, params={"state": begin(), "code": failure})
            assert reply.status_code == 403
            assert reply.json()["detail"]["code"] == "AUTH_EMAIL_UNVERIFIED"
        assert (
            browser.get(callback, params={"state": begin(), "code": "missing_sid"}).status_code
            == 400
        )
        register = browser.get("/api/v1/auth/register")
        attempt.update(parse_qs(urlsplit(register.headers["location"]).query))
        assert attempt["prompt"] == ["create"]
        assert attempt["scope"] == ["openid profile email"]
        reply = browser.get(callback, params={"state": attempt["state"][0], "code": "real"})
        assert reply.status_code == 302
        account = browser.get("/api/v1/auth/me").json()
        assert account["user_id"] == user["user_id"]
        assert account["account_management_enabled"] is True
        assert account["email"] == "account@example.test" and account["email_verified"] is True
        assert account["avatar_url"] == "https://id.example/avatar.png"
        assert (
            browser.post("/api/v1/auth/profile/edit", headers={"X-CSRF-Token": ""}).status_code
            == 403
        )

        def profile_edit(code, **params):
            response = browser.post(
                "/api/v1/auth/profile/edit", headers={"X-CSRF-Token": account["csrf_token"]}
            )
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            attempt.update(parse_qs(urlsplit(response.json()["authorization_url"]).query))
            assert attempt.pop("kc_action") == ["UPDATE_PROFILE"]
            return browser.get(
                callback, params={"state": attempt["state"][0], "code": code, **params}
            )

        assert profile_edit("other_user", kc_action_status="cancelled").status_code == 400
        cancelled = profile_edit("renamed", kc_action_status="cancelled")
        assert cancelled.headers["location"] == f"{origin}/account"
        assert browser.get("/api/v1/auth/me").json() == account
        assert (
            profile_edit("renamed", kc_action_status="success").headers["location"]
            == f"{origin}/account"
        )
        updated = browser.get("/api/v1/auth/me").json()
        assert updated["user_id"] == user["user_id"]
        assert updated["display_name"] == "更新姓名" and updated["email"] == "renamed@example.test"

        def notify(*, drop=None, key=private_key, **fields):
            now = datetime.now(UTC)
            claims = {
                "iss": ISSUER,
                "sub": subject,
                "aud": CLIENT_ID,
                "jti": str(uuid4()),
                "sid": oidc_sid,
                "events": {LOGOUT_EVENT: {}},
                "iat": now,
                "exp": now + timedelta(minutes=2),
                **fields,
            }
            if drop:
                claims.pop(drop)
            token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})
            return browser.post("/api/v1/auth/backchannel-logout", data={"logout_token": token})

        for result in (
            notify(nonce=None),
            notify(aud="different"),
            notify(drop="sid"),
            notify(drop="jti"),
            notify(events={LOGOUT_EVENT: False}),
            notify(key=wrong_key),
            notify(
                iat=datetime.now(UTC) - timedelta(minutes=5),
                exp=datetime.now(UTC) - timedelta(minutes=3),
            ),
        ):
            assert result.status_code == 400 and result.json() == {"error": "invalid_request"}
        result = browser.post(
            "/api/v1/auth/backchannel-logout", data={"logout_token": ["one", "two"]}
        )
        assert result.status_code == 400
        assert notify(sub="unrelated").status_code == 200
        assert browser.get("/api/v1/auth/me").status_code == 200
        pending = browser.post(
            "/api/v1/auth/profile/edit", headers={"X-CSRF-Token": updated["csrf_token"]}
        )
        assert pending.status_code == 200
        attempt.update(parse_qs(urlsplit(pending.json()["authorization_url"]).query))
        logout = browser.post(
            "/api/v1/auth/logout/oidc", headers={"X-CSRF-Token": updated["csrf_token"]}
        )
        assert logout.status_code == 200 and "Max-Age=0" in logout.headers["set-cookie"]
        provider_location = urlsplit(logout.json()["provider_logout_url"])
        assert provider_location.path == "/logout"
        assert parse_qs(provider_location.query) == {
            "client_id": [CLIENT_ID],
            "post_logout_redirect_uri": [f"{origin}/"],
        }
        assert browser.get("/api/v1/auth/me").status_code == 401
        assert (
            browser.get(
                callback, params={"state": attempt["state"][0], "code": "renamed"}
            ).status_code
            == 400
        )

        assert browser.get(callback, params={"state": begin(), "code": "real"}).status_code == 302
        active = browser.get("/api/v1/auth/me").json()
        advertise_logout = False
        logout = browser.post(
            "/api/v1/auth/logout/oidc", headers={"X-CSRF-Token": active["csrf_token"]}
        )
        assert logout.status_code == 200 and logout.json()["provider_logout_url"] is None
        assert browser.get("/api/v1/auth/me").status_code == 401

        assert browser.get(callback, params={"state": begin(), "code": "real"}).status_code == 302
        jti = str(uuid4())
        for _ in range(2):
            logout = notify(jti=jti)
            assert logout.status_code == 200 and logout.content == b""
        assert browser.get("/api/v1/auth/me").status_code == 401
        assert browser.get(callback, params={"state": begin(), "code": "real"}).status_code == 400
    finally:
        client.portal.call(upstream.aclose)

        async def cleanup():
            async with app.state.sessions.begin() as session:
                await session.execute(
                    delete(LogoutReceiptRow).where(LogoutReceiptRow.sid == oidc_sid)
                )
                await session.execute(delete(UserRow).where(UserRow.subject == subject))

        client.portal.call(cleanup)


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
