import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from travelmind.research.adapters import text_value
from travelmind.research.http import ProviderClient, ProviderFailure, invalid_response
from travelmind.schemas.domain import Evidence

BASE = "https://api.cnkgraph.com"
HEADERS = {"Accept-Language": "zh-hant"}
MAX_VOLUMES = 2
EXCERPT_LIMIT = 1200
VOLUME_ID = re.compile(r"KR[0-9A-Za-z]+_[0-9A-Za-z]+")
TRAVELOGUE_OR_GAZETTEER = re.compile(r"[遊游][記记]|[府州縣县鄉乡省]志|一[統统]志")
GEOGRAPHY = re.compile(r"地理[類类]|方志|輿地|舆地")
GEOGRAPHIC_COMPILATION = re.compile(r"方[輿舆]|名[勝胜]")
MARKUP = re.compile(r"<(?:md|pb):[^>\n]+>")
PAGE_BREAK = re.compile(r"<pb:[^>\n]+?_(\d*)-(\d+[A-Za-z]?)>")
PAGE_LABEL = re.compile(r"(\d*)-(\d+[A-Za-z]?)")


def records(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))


def clean_text(value: object) -> str:
    return MARKUP.sub("", text_value(value) or "").strip()


@dataclass(frozen=True)
class Passage:
    priority: int
    title: str
    volume: str
    volume_id: str
    page: str | None
    match: str
    preview: str


def passages(categories: list[object]) -> Iterator[Passage]:
    for group in records(categories):
        geography = GEOGRAPHY.search(text_value(group.get("Category")) or "")
        for book in records(group.get("Books")):
            title = text_value(book.get("Book"))
            if title is None:
                continue
            if TRAVELOGUE_OR_GAZETTEER.search(title):
                priority = 0
            elif geography:
                priority = 1
            elif GEOGRAPHIC_COMPILATION.search(title):
                priority = 2
            else:
                continue
            for volume in records(book.get("Volumes")):
                name = text_value(volume.get("Volume"))
                volume_id = text_value(volume.get("VolumeId"))
                if name is None or volume_id is None or not VOLUME_ID.fullmatch(volume_id):
                    continue
                for page in records(volume.get("Pages")):
                    match = clean_text(page.get("MatchedText"))
                    before = clean_text(page.get("PreviousText"))[-500:]
                    after = clean_text(page.get("LaterText"))[:500]
                    if match and (before or after):
                        preview = " ".join(part for part in (before, match, after) if part)
                        yield Passage(
                            priority,
                            title,
                            name,
                            volume_id,
                            text_value(page.get("Page")),
                            match,
                            preview[:EXCERPT_LIMIT],
                        )


def original_page(text: str, passage: Passage) -> str | None:
    label = PAGE_LABEL.fullmatch(passage.page or "")
    if label is None:
        return None
    volume, folio = label.groups()
    pages = PAGE_BREAK.split(text)
    match = re.sub(r"\s+", "", passage.match)
    for number, page, body in zip(pages[1::3], pages[2::3], pages[3::3], strict=True):
        if number.lstrip("0") == volume.lstrip("0") and page == folio:
            original = clean_text(body)
            if match in re.sub(r"\s+", "", original):
                if len(original) <= EXCERPT_LIMIT:
                    return original
                # Long pages can contain several passages. Keep the window around the hit.
                start = original.find(passage.match)
                if start >= 0:
                    start = max(0, start - EXCERPT_LIMIT // 3)
                    return original[start : start + EXCERPT_LIMIT].strip()
    return None


class CnkgraphBooks:
    """Search and read historical Chinese texts from the provider's public research API."""

    def __init__(self, http: ProviderClient):
        self.http = http

    async def search(self, query: str, limit: int) -> list[Evidence]:
        payload = await self.http.request(
            "POST",
            f"{BASE}/Api/Book/Find",
            json={"key": query, "pageNo": 0},
            headers=HEADERS,
            accepted_status=404,
        )
        categories = payload.get("Result")
        count = payload.get("Count")
        if not payload.get("Error") and (
            payload.get("Message") == "没找到" or (type(count) is int and count == 0)
        ):
            return []
        if payload.get("Error") or not isinstance(categories, list):
            raise invalid_response("cnkgraph")
        selected = {}
        for passage in sorted(passages(categories), key=lambda hit: (hit.priority, not hit.page)):
            selected.setdefault(passage.volume_id, passage)
            if len(selected) == min(limit, MAX_VOLUMES):
                break
        return [await self._read(passage) for passage in selected.values()]

    async def _read(self, passage: Passage) -> Evidence:
        url = f"{BASE}/api/book/volume/{passage.volume_id}"
        original = None
        edition = None
        try:
            payload = await self.http.request("GET", url, headers=HEADERS)
            text = text_value(payload.get("Text"))
            if payload.get("VolumeId") == passage.volume_id and text:
                original = original_page(text, passage)
                if original:
                    edition = re.search(r"(?m)^#\+PROPERTY: BASEEDITION\s+([^\s]+)", text)
        except ProviderFailure:
            # Find also gives source text, including the match and its surrounding context.
            pass
        locator = ["古籍文献知识图谱网", passage.volume]
        if passage.page:
            locator.append(f"页码 {passage.page}")
        if edition:
            locator.append(f"底本 {edition[1]}")
        locator.append(passage.volume_id)
        if not original:
            locator.append("检索摘录")
        return Evidence(
            evidence_id=uuid4(),
            source_type="book",
            source_title=passage.title,
            source_url=url,
            source_locator=" · ".join(locator),
            retrieved_at=datetime.now(UTC),
            excerpt=original or passage.preview,
        )
