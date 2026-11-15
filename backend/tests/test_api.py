import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from travelmind.api import health
from travelmind.config import Settings
from travelmind.db.connection import create_database_engine
from travelmind.main import create_app
from travelmind.schemas.events import AgentEvent
from travelmind.server_logging import AccessLogFilter


def test_access_logs_drop_query_parameters():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:54321", "GET", "/api/v1/auth/callback?state=test-value", "1.1", 302),
        None,
    )
    assert AccessLogFilter().filter(record)
    assert record.getMessage() == '127.0.0.1:54321 - "GET /api/v1/auth/callback HTTP/1.1" 302'


def test_database_errors_hide_parameter_values():
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://localhost/travelmind")
    assert create_database_engine(settings).sync_engine.hide_parameters


def test_service_starts_without_external_dependencies():
    settings = Settings(_env_file=None, database_url=None, redis_url=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        readiness = client.get("/health/ready")
        assert readiness.status_code == 503
        assert readiness.json()["postgres"] == "not_configured"
        assert readiness.json()["redis"] == "not_configured"
        response = client.get(f"/api/v1/tasks/{uuid4()}")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("postgres_ok", "redis_status", "status_code"),
    [
        (True, "ok", 200),
        (True, "not_configured", 200),
        (True, "unavailable", 200),
        (False, "ok", 503),
    ],
)
def test_readiness_requires_postgres_and_reports_redis_status(
    monkeypatch, postgres_ok, redis_status, status_code
):
    async def check_postgres(_engine):
        if not postgres_ok:
            raise OSError("PostgreSQL is unavailable")

    async def check_redis(_redis):
        if redis_status == "unavailable":
            raise OSError("Redis is unavailable")

    monkeypatch.setattr(health, "check_postgres", check_postgres)
    monkeypatch.setattr(health, "check_redis", check_redis)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://localhost/travelmind",
        redis_url=None if redis_status == "not_configured" else "redis://localhost",
    )
    with TestClient(create_app(settings)) as client:
        readiness = client.get("/health/ready")
    assert readiness.status_code == status_code
    assert readiness.json()["status"] == ("ready" if postgres_ok else "unavailable")
    assert readiness.json()["postgres"] == ("ok" if postgres_ok else "unavailable")
    assert readiness.json()["redis"] == redis_status


def test_openapi_preserves_sse_task_and_idempotency_contracts():
    settings = Settings(_env_file=None, database_url=None, redis_url=None)
    with TestClient(create_app(settings)) as client:
        spec = client.get("/openapi.json").json()
    stream = spec["paths"]["/api/v1/tasks/{task_id}/events"]["get"]
    content = stream["responses"]["200"]["content"]
    assert content["text/event-stream"]["schema"]["$ref"] == "#/components/schemas/AgentEvent"
    for status in ("404", "503"):
        assert set(stream["responses"][status]["content"]) == {"application/json"}
    assert (
        spec["components"]["schemas"]["AgentEvent"]["properties"]["payload"]["discriminator"][
            "propertyName"
        ]
        == "type"
    )

    schemas = spec["components"]["schemas"]
    task = schemas["TaskRead"]
    assert {"pause_requested_at", "pending_clarification"} <= set(task["properties"])
    assert {"pause_requested_at", "pending_clarification"}.isdisjoint(task["required"])
    assert "PendingClarification" in schemas
    parameters = spec["paths"]["/api/v1/tasks"]["post"]["parameters"]
    key = next(item for item in parameters if item["name"].lower() == "idempotency-key")
    assert not key["required"]
    assert {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": "^[!-~]+$",
    } in key["schema"]["anyOf"]

    with pytest.raises(ValidationError):
        AgentEvent.model_validate(
            {
                "task_id": uuid4(),
                "trace_id": uuid4(),
                "event_id": uuid4(),
                "sequence": 1,
                "occurred_at": datetime.now(UTC),
                "payload": {"type": "evidence.added", "status": "queued"},
            }
        )
