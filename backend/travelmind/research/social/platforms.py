import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from threading import Event
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from travelmind.research.social.schemas import (
    MAX_COMMENTS,
    MAX_POSTS,
    ImportRequest,
    Platform,
    SocialComment,
    SocialEvidenceRecord,
    SourceID,
    canonical_url,
    keyword_key,
)

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
PUBLIC_ID = TypeAdapter(SourceID)
CONTACT = re.compile(
    r"https?://\S+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)"
    r"|(?<!\d)\d{17}[\dXx](?!\d)|(?<!\w)@[\w-]+"
)


class ImportStopped(Exception):
    pass


def clean(value: object, size: int) -> str:
    if not isinstance(value, str):
        return ""
    text = unescape(value)
    text = re.sub(r"</?[a-zA-Z][^<>]{0,300}>", " ", text)
    return " ".join(CONTACT.sub("[已隐去]", text).split())[:size].strip()


def parse_count(value: object) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def parse_time(value: object) -> datetime | None:
    stamp = parse_count(value)
    if not stamp:
        return None
    try:
        return datetime.fromtimestamp(stamp / 1000 if stamp >= 100_000_000_000 else stamp, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def public_id(value: object) -> str:
    return PUBLIC_ID.validate_python(str(value) if type(value) is int else value)


@dataclass(frozen=True)
class PlatformAdapter:
    id_field: str
    text_field: str
    time_field: str
    count_field: str
    content_type: str
    comment_likes: str = "like_count"

    def post(self, row: dict, request: ImportRequest) -> SocialEvidenceRecord:
        post_id = public_id(row.get(self.id_field))
        url = canonical_url(request.platform, post_id)
        title = clean(row.get("title"), 200)
        content = clean(row.get(self.text_field), 1600) or title
        kind = self.content_type
        if request.platform == "douyin" and row.get("note_download_url"):
            kind = "note"
        return SocialEvidenceRecord(
            evidence_id=uuid5(NAMESPACE_URL, url),
            platform=request.platform,
            post_id=post_id,
            content_type=kind,
            title=title or content[:200],
            content=content,
            original_url=url,
            published_at=parse_time(row.get(self.time_field)),
            collected_at=request.collected_at,
            like_count=parse_count(row.get("liked_count")),
            comment_count=parse_count(row.get(self.count_field)),
            keyword=request.keyword,
            collection_status="partial",
        )

    def comment(self, row: dict) -> SocialComment:
        parent = row.get("parent_comment_id")
        comment_id = public_id(row.get("comment_id"))
        parent_id = public_id(parent) if parent not in (None, "", 0, "0") else None
        return SocialComment(
            comment_id=comment_id,
            parent_comment_id=parent_id if parent_id != comment_id else None,
            content=clean(row.get("content"), 320),
            published_at=parse_time(row.get("create_time")),
            like_count=parse_count(row.get(self.comment_likes)),
        )


PLATFORMS: dict[Platform, PlatformAdapter] = {
    "xiaohongshu": PlatformAdapter("note_id", "desc", "time", "comment_count", "note"),
    "bilibili": PlatformAdapter("video_id", "desc", "create_time", "video_comment", "video"),
    "weibo": PlatformAdapter(
        "note_id", "content", "create_time", "comments_count", "post", "comment_like_count"
    ),
    "douyin": PlatformAdapter("aweme_id", "desc", "create_time", "comment_count", "video"),
}


def json_rows(path: Path, stop: Event) -> Iterator[dict | None]:
    if path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("invalid import file")
    size = 0
    with path.open("rb") as stream:
        while raw := stream.readline(MAX_LINE_BYTES + 1):
            if stop.is_set():
                raise ImportStopped()
            size += len(raw)
            if len(raw) > MAX_LINE_BYTES or size > MAX_FILE_BYTES:
                raise ValueError("import file exceeds its budget")
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8-sig"))
            except (ValueError, RecursionError):
                row = None
            yield row if isinstance(row, dict) else None


def keep_comment(target: dict[str, SocialComment], keys: set[str], comment: SocialComment) -> None:
    key = keyword_key(comment.content)
    if len(target) < MAX_COMMENTS and comment.comment_id not in target and key not in keys:
        target[comment.comment_id] = comment
        keys.add(key)


def normalize_record(row: dict, request: ImportRequest) -> SocialEvidenceRecord:
    item = SocialEvidenceRecord.model_validate(row)
    if (
        item.platform != request.platform
        or keyword_key(item.keyword) != keyword_key(request.keyword)
        or item.collected_at != request.collected_at
    ):
        raise ValueError("normalized record does not match batch metadata")
    comments: dict[str, SocialComment] = {}
    keys: set[str] = set()
    for comment in item.comments:
        content = clean(comment.content, 320)
        if content:
            keep_comment(comments, keys, comment.model_copy(update={"content": content}))
    return SocialEvidenceRecord.model_validate(
        {
            **item.model_dump(),
            "title": clean(item.title, 200),
            "content": clean(item.content, 1600),
            "author_display_name": None,
            "comments": list(comments.values()),
        }
    )


def load_export(
    batch: Path, request: ImportRequest, stop: Event | None = None
) -> tuple[list[SocialEvidenceRecord], int]:
    if batch.resolve() != batch:
        raise ValueError("invalid import directory")
    stop = stop if stop is not None else Event()
    adapter = PLATFORMS[request.platform]
    normalized = request.format == "normalized"
    posts: dict[str, SocialEvidenceRecord] = {}
    errors = 0
    keyword = keyword_key(request.keyword)
    for row in json_rows(batch / ("evidence.jsonl" if normalized else "contents.jsonl"), stop):
        try:
            if row is None:
                raise ValueError("invalid post row")
            if not normalized and (source := row.get("source_keyword")):
                if not isinstance(source, str) or keyword_key(source) != keyword:
                    raise ValueError("post keyword does not match batch")
            item = normalize_record(row, request) if normalized else adapter.post(row, request)
            posts.setdefault(item.post_id, item)
        except ValueError:
            errors += 1
        if len(posts) == MAX_POSTS:
            break
    path = batch / "comments.jsonl"
    if normalized or not posts or not path.exists():
        return list(posts.values()), errors

    comments: dict[str, dict[str, SocialComment]] = defaultdict(dict)
    keys: dict[str, set[str]] = defaultdict(set)
    for row in json_rows(path, stop):
        try:
            if row is None:
                raise ValueError("invalid comment row")
            post_id = public_id(row.get(adapter.id_field))
            if post_id in posts:
                keep_comment(comments[post_id], keys[post_id], adapter.comment(row))
        except ValueError:
            errors += 1
        if all(len(comments[key]) == MAX_COMMENTS for key in posts):
            break
    for key, item in posts.items():
        item.comments = list(comments[key].values())
        if errors == 0 and (item.comments or item.comment_count == 0):
            item.collection_status = "collected"
    return list(posts.values()), errors
