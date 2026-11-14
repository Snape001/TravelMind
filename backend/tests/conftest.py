import os

import pytest
from fastapi.testclient import TestClient

from travelmind.config import Settings
from travelmind.main import create_app


@pytest.fixture
def client():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated migrated test database")
    api = create_app(Settings(_env_file=None, database_url=database_url, redis_url=None))
    with TestClient(api) as transport:
        yield transport
