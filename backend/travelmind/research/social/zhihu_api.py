"""Bounded collection through Zhihu's documented developer APIs."""

import argparse
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from travelmind.research.social.platforms import (
    clean,
    keep_comment,
    parse_count,
    parse_time,
    public_id,
    zhihu_record,
)
from travelmind.research.social.schemas import (
    MAX_COMMENTS,
    ImportRequest,
    Keyword,
    SocialComment,
    SocialEvidenceRecord,
    keyword_key,
    zhihu_source,
)
from travelmind.research.social.zhihu_client import ZhihuError, ZhihuOfficialClient

Mode = Literal["search", "answers", "owned"]
MAX_PAGES = 2
PAGE_SIZE = 5


def rows(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ZhihuError("INVALID_RESPONSE")
    return value


def pages(call, endpoint: str, target: dict) -> Iterator[list[dict]]:
    offset = 0
    for _ in range(MAX_PAGES):
        page = call(endpoint, **target, Offset=offset, Limit=PAGE_SIZE)
        paging = page.get("Paging")
        if not isinstance(paging, dict) or type(paging.get("IsEnd")) is not bool:
            raise ZhihuError("INVALID_PAGINATION")
        end = paging["IsEnd"]
        next_offset = paging.get("NextOffset")
        if not end and (type(next_offset) is not int or next_offset <= offset):
            raise ZhihuError("INVALID_PAGINATION")
        yield rows(page.get("Items"))[:PAGE_SIZE]
        if end:
            break
        offset = next_offset


def featured_comments(value: object) -> list[SocialComment]:
    comments = {}
    keys: set[str] = set()
    for entry in rows(value or []):
        content = clean(entry.get("Content"), 320)
        if content:
            excerpt_id = hashlib.sha256(keyword_key(content).encode()).hexdigest()[:32]
            keep_comment(
                comments,
                keys,
                SocialComment(
                    comment_id=f"excerpt-{excerpt_id}",
                    content=content,
                ),
            )
        if len(comments) == MAX_COMMENTS:
            break
    return list(comments.values())


def record(item: dict, keyword: str, collected: datetime, mode: Mode) -> SocialEvidenceRecord:
    body = {"search": "ContentText", "answers": "Summary", "owned": "Body"}[mode]
    text = clean(item.get(body), 1600)
    if mode != "search" and not text:
        raise ValueError("Zhihu body is unavailable")
    kind = "answer" if mode == "answers" else str(item.get("ContentType")).lower()
    try:
        result = zhihu_record(
            {
                "title": item.get("Title"),
                "content_text": text,
                "content_type": kind,
                "content_url": item.get("Url"),
                "content_id": item.get("ContentID" if mode == "search" else "ContentToken"),
                "created_time": item.get("CreateTime"),
                "voteup_count": item.get("VoteUpCount"),
                "comment_count": item.get("CommentCount"),
                "author_display_name": item.get("AuthorName"),
            },
            keyword,
            collected,
            scope="detail" if mode == "owned" else "excerpt",
        )
    except ValueError:
        raise ZhihuError("INVALID_RESPONSE") from None
    if mode == "search":
        result.comments = featured_comments(item.get("CommentInfoList"))
    return result


def comment(item: dict, parent: str | None = None) -> SocialComment:
    if not isinstance(item, dict):
        raise ZhihuError("INVALID_RESPONSE")
    comment_id = public_id(item.get("ID"))
    reply = item.get("ReplyID")
    target = public_id(reply) if reply not in (None, 0, "0", "") else parent
    root = item.get("RootID")
    root_id = public_id(root) if root not in (None, 0, "0", "") else parent
    return SocialComment(
        comment_id=comment_id,
        parent_comment_id=target if target != comment_id else parent,
        root_comment_id=root_id if root_id != comment_id else None,
        author_token=clean(item.get("AuthorToken"), 200) or None,
        content=clean(item.get("Content"), 320),
        published_at=parse_time(item.get("CreatedAt")),
        like_count=parse_count(item.get("LikeCount")),
    )


def collect(
    call, mode: Mode, keyword: str, collected: datetime, url: str | None = None
) -> list[SocialEvidenceRecord]:
    if mode == "search":
        items = rows(call("content/zhihu_search", Query=keyword, Count=10).get("Items"))[:10]
    else:
        try:
            post_id, target = zhihu_source(url or "")
        except ValueError:
            raise ZhihuError("INVALID_SOURCE_URL") from None
        if mode == "answers" and post_id.startswith("question-"):
            items = []
            for page in pages(call, "content/question_answers", {"QuestionUrl": target}):
                items.extend(page)
        elif mode == "owned" and post_id.startswith("answer-"):
            detail = call("user/content_detail", ContentUrl=target)
            result = record(detail, keyword, collected, mode)
            if result.post_id != post_id:
                raise ZhihuError("INVALID_RESPONSE")
            comments = {}
            seen: set[str] = set()
            for page in pages(call, "user/content_comments", {"ContentUrl": target}):
                for thread in page:
                    root_comment = comment(thread.get("Comment") or {})
                    keep_comment(comments, seen, root_comment, deduplicate_text=False)
                    for child in rows(thread.get("Children") or []):
                        keep_comment(
                            comments,
                            seen,
                            comment(child, root_comment.comment_id),
                            deduplicate_text=False,
                        )
                        if len(comments) >= MAX_COMMENTS:
                            break
                    if len(comments) >= MAX_COMMENTS:
                        break
                if len(comments) >= MAX_COMMENTS:
                    break
            result.comments = list(comments.values())
            return [result]
        else:
            raise ZhihuError("INVALID_SOURCE_URL")
    by_id = {}
    for item in items:
        if mode == "search" and str(item.get("ContentType")).lower() not in ("answer", "question"):
            continue
        result = record(item, keyword, collected, mode)
        if mode == "answers" and not str(result.original_url).startswith(target + "/answer/"):
            raise ZhihuError("INVALID_RESPONSE")
        by_id.setdefault(result.post_id, result)
    return list(by_id.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quota", action="store_true", help="check authentication and free quota")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--keyword")
    parser.add_argument("--mode", choices=("search", "answers", "owned"), default="search")
    parser.add_argument("--url")
    args = parser.parse_args()
    if not args.quota and (args.data is None or args.keyword is None):
        parser.error("--data and --keyword are required for collection")
    try:
        client = ZhihuOfficialClient.from_environment()
        if args.quota:
            print(json.dumps(client.quota(), ensure_ascii=True))
            return
    except ZhihuError as exc:
        parser.exit(1, f"Zhihu API {exc}\n")
    try:
        keyword = TypeAdapter(Keyword).validate_python(args.keyword)
    except ValidationError:
        parser.exit(2, "INVALID_KEYWORD\n")
    collected = datetime.now(UTC)
    try:
        results = collect(client, args.mode, keyword, collected, args.url)
    except ZhihuError as exc:
        parser.exit(1, f"Zhihu API {exc}\n")
    except (ValueError, TypeError, AttributeError):
        parser.exit(1, "Zhihu API INVALID_RESPONSE\n")
    request = ImportRequest(
        batch_id=uuid4(),
        platform="zhihu",
        keyword=keyword,
        collected_at=collected,
        format="normalized",
    )
    batch = args.data / "inbox" / str(request.batch_id)
    batch.mkdir(parents=True, exist_ok=False)
    (batch / "evidence.jsonl").write_text(
        "".join(result.model_dump_json() + "\n" for result in results), encoding="utf-8"
    )
    print(request.model_dump_json())


if __name__ == "__main__":
    main()
