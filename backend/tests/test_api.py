from uuid import uuid4

from fastapi.testclient import TestClient

from travelmind.config import Settings
from travelmind.main import create_app


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


def test_openapi_documents_sse_as_agent_event():
    with TestClient(create_app()) as client:
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


def test_task_read_and_create_header_publish_compatible_openapi_schema():
    settings = Settings(_env_file=None, database_url=None, redis_url=None)
    with TestClient(create_app(settings)) as client:
        spec = client.get("/openapi.json").json()

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
