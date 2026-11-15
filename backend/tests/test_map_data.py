from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from travelmind.db.models import ReportRow
from travelmind.schemas.map_data import MapData, MapDay, MapStop


def test_map_data_rejects_dangling_poi_references():
    with pytest.raises(ValidationError, match="unknown POI"):
        MapData(
            task_id=uuid4(),
            status="partial",
            days=[MapDay(day_number=1, stops=[MapStop(poi_id="foreign", suggested_minutes=45)])],
        )


@pytest.mark.postgres
def test_task_route_and_report_map_reads_keep_owner_and_legacy_semantics(client):
    task = client.post("/api/v1/tasks", json={"destination": "泉州"}).json()
    base = f"/api/v1/tasks/{task['task_id']}"
    assert client.get(f"{base}/routes").json() == {"task_id": task["task_id"], "routes": []}
    assert (
        client.get(f"{base}/reports/{uuid4()}/map-data").json()["detail"]["code"]
        == "REPORT_NOT_FOUND"
    )
    report_id = uuid4()

    async def legacy_report():
        async with client.app.state.sessions.begin() as session:
            session.add(
                ReportRow(
                    task_id=UUID(task["task_id"]),
                    report_id=report_id,
                    version=1,
                    content={
                        "title": "历史报告",
                        "sections": [
                            {
                                "heading": "旧内容",
                                "body": "旧正文",
                                "author_agent_ids": ["history_culture"],
                            }
                        ],
                    },
                )
            )

    client.portal.call(legacy_report)
    assert client.get(f"{base}/reports/{report_id}").json()["content"]["title"] == "历史报告"
    historical = client.get(f"{base}/reports/{report_id}/map-data")
    assert historical.status_code == 200
    assert historical.json()["status"] == "unavailable"
