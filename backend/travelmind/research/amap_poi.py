import re

from travelmind.research.amap_client import AmapClient
from travelmind.runtime.execution import RunError
from travelmind.schemas.phase4_intake import AdministrativeDivisions, Destination, Gcj02Point

MAX_KEYWORD_LENGTH = 80
MAX_CANDIDATES = 10
_POI_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _code(value: object, pattern: str) -> str | None:
    value = _text(value)
    return value if value and re.fullmatch(pattern, value) else None


def _items(data: dict, field: str) -> list[dict]:
    items = data.get(field)
    if not isinstance(items, list):
        if items is None and data.get("count") in ("0", 0):
            return []
        raise RunError("AMAP_INVALID_RESPONSE", "高德返回了无法读取的地点列表", retryable=True)
    return [item for item in items if isinstance(item, dict)]


def _destination(item: dict) -> Destination | None:
    poi_id, name, location = (_text(item.get(key)) for key in ("id", "name", "location"))
    if not poi_id or not _POI_ID.fullmatch(poi_id) or not name or not location:
        return None
    try:
        longitude, latitude = map(float, location.split(","))
        return Destination(
            poi_id=poi_id,
            name=name,
            address=_text(item.get("address")),
            administrative=AdministrativeDivisions(
                province=_text(item.get("pname")),
                city=_text(item.get("cityname")),
                district=_text(item.get("adname")),
                adcode=_code(item.get("adcode"), r"[0-9]{6}"),
                citycode=_code(item.get("citycode"), r"[0-9]{3,4}"),
            ),
            coordinates=Gcj02Point(longitude=longitude, latitude=latitude),
        )
    except ValueError:
        return None


class AmapPoi:
    def __init__(self, client: AmapClient):
        self.client = client

    async def search(self, keyword: str) -> list[Destination]:
        keyword = keyword.strip()
        if not 1 <= len(keyword) <= MAX_KEYWORD_LENGTH:
            raise RunError("AMAP_INVALID_KEYWORD", "请输入不超过 80 字的地点名称，可加上城市或区县")
        payload = await self.client.get(
            "/v5/place/text", keywords=keyword, page_size=MAX_CANDIDATES
        )
        found = self._places(payload)
        if not any(place.name.casefold() == keyword.casefold() for place in found.values()):
            try:
                # Only promote input tips whose IDs are verified by the POI detail service.
                tips = await self.client.get(
                    "/v3/assistant/inputtips", keywords=keyword, datatype="poi"
                )
                ids = list(
                    dict.fromkeys(
                        poi_id
                        for tip in _items(tips, "tips")
                        if (poi_id := _text(tip.get("id")))
                        and _POI_ID.fullmatch(poi_id)
                        and poi_id not in found
                    )
                )[:5]
                if ids:
                    advised = await self.client.get("/v5/place/detail", id="|".join(ids))
                    resolved = self._places(advised)
                    found = {**{key: resolved[key] for key in ids if key in resolved}, **found}
            except RunError:
                if not found:
                    raise
        return list(found.values())[:MAX_CANDIDATES]

    @staticmethod
    def _places(payload: dict) -> dict[str, Destination]:
        places = {}
        for item in _items(payload, "pois"):
            if destination := _destination(item):
                places.setdefault(destination.poi_id, destination)
        return places
