import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.api.oidc import AuthSettings
from travelmind.config import Settings
from travelmind.db.auth import csrf_token, start_session
from travelmind.db.models import TaskRow, UserRow
from travelmind.main import create_app

ORIGIN = "https://travelmind.example"


@pytest.fixture
def client():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated migrated test database")
    api = create_app(
        Settings(_env_file=None, database_url=database_url, redis_url=None),
        auth_settings=AuthSettings(_env_file=None, public_origin=ORIGIN),
    )
    with TestClient(api, base_url=ORIGIN) as transport:
        subject = str(uuid4())

        async def seed():
            async with api.state.sessions.begin() as session:
                return await start_session(
                    session, "https://issuer.example", subject, None, lifetime=3600, old_token=None
                )

        token = transport.portal.call(seed)
        transport.cookies.set(SESSION_COOKIE, token)
        transport.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf_token(token)})
        try:
            yield transport
        finally:

            async def cleanup():
                async with api.state.sessions.begin() as session:
                    users = UserRow.__table__
                    await session.execute(
                        delete(TaskRow).where(
                            TaskRow.owner_id.in_(
                                select(users.c.user_id).where(users.c.subject == subject)
                            )
                        )
                    )
                    await session.execute(delete(UserRow).where(UserRow.subject == subject))

            transport.portal.call(cleanup)
