import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from unicodedata import normalize
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, Field, HttpUrl, StringConstraints, model_validator

from travelmind.schemas.domain import Contract, Evidence, ShortText, Text
from travelmind.schemas.phase4_sources import SocialEvidence
from travelmind.schemas.phase4_sources import SocialPlatform as Platform

Keyword = Annotated[Text, Field(max_length=120)]
SourceID = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
ContentScope = Literal["excerpt", "detail"]
MAX_POSTS = 15
MAX_COMMENTS = 12
SOURCE_URLS: dict[str, str] = {
    "xiaohongshu": "https://www.xiaohongshu.com/explore/{}",
    "bilibili": "https://www.bilibili.com/video/av{}",
    "weibo": "https://m.weibo.cn/detail/{}",
    "douyin": "https://www.douyin.com/video/{}",
}
PLATFORM_NAMES: dict[Platform, str] = {
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "weibo": "微博",
    "douyin": "抖音",
    "zhihu": "知乎",
}
ZHIHU_ORIGIN = "https://www.zhihu.com"


def zhihu_source(value: str, *, attribution: bool = False) -> tuple[str, str]:
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.netloc != "www.zhihu.com":
        raise ValueError("expected a Zhihu question or answer URL")
    question = re.fullmatch(r"/question/([0-9]{1,24})", parts.path)
    answer = re.fullmatch(r"(?:/question/[0-9]{1,24})?/answer/([0-9]{1,24})", parts.path)
    if question is None and answer is None:
        raise ValueError("expected a Zhihu question or answer URL")
    post_id = f"answer-{answer[1]}" if answer else f"question-{question[1]}"
    url = ZHIHU_ORIGIN + parts.path
    if attribution:
        fields = dict(
            re.findall(r"(?:^|&)(utm_medium|utm_source)=([A-Za-z0-9_-]{1,64})(?=&|$)", parts.query)
        )
        if fields.get("utm_medium") == "openapi_platform":
            url += "?" + urlencode(fields)
    return post_id, url


def social_evidence_id(platform: Platform, post_id: str, url: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"{ZHIHU_ORIGIN}/{post_id.replace('-', '/', 1)}" if platform == "zhihu" else url,
    )


def keyword_key(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())


def canonical_url(platform: Platform, post_id: str) -> str:
    if platform in ("bilibili", "douyin", "weibo") and not post_id.isdecimal():
        raise ValueError("expected a numeric post ID from the pinned export")
    return SOURCE_URLS[platform].format(post_id)


class SocialComment(Contract):
    comment_id: SourceID
    parent_comment_id: SourceID | None = None
    content: Annotated[Text, Field(max_length=320)]
    published_at: AwareDatetime | None = None
    like_count: Count | None = None


class SocialEvidenceRecord(Contract):
    """Sidecar JSONL record. The frozen Phase 4 handoff and published Evidence stay compatible."""

    evidence_id: UUID
    platform: Platform
    post_id: SourceID
    content_type: Literal["note", "video", "post", "question", "answer"]
    provider: Literal["mediacrawler_export", "zhihu_open_api"] = "mediacrawler_export"
    content_scope: ContentScope | None = None
    title: ShortText
    content: Annotated[Text, Field(max_length=1600)]
    author_display_name: ShortText | None = None
    original_url: HttpUrl
    published_at: AwareDatetime | None = None
    collected_at: AwareDatetime
    like_count: Count | None = None
    comment_count: Count | None = None
    comments: list[SocialComment] = Field(default_factory=list, max_length=MAX_COMMENTS)
    keyword: Keyword
    collection_status: Literal["collected", "partial"]

    @model_validator(mode="after")
    def validate_source(self):
        official = self.provider == "zhihu_open_api"
        if official != (self.content_scope is not None):
            raise ValueError("scope is required for Zhihu Open API results")
        if self.platform == "zhihu":
            post_id, url = zhihu_source(str(self.original_url), attribution=official)
            if post_id != self.post_id or self.content_type != post_id.split("-", 1)[0]:
                raise ValueError("Zhihu content type and ID must match the URL")
        else:
            if official or self.content_type in ("question", "answer"):
                raise ValueError("unexpected Zhihu fields for another platform")
            url = canonical_url(self.platform, self.post_id)
        if str(self.original_url) != url or self.evidence_id != social_evidence_id(
            self.platform, self.post_id, url
        ):
            raise ValueError("social URL and evidence ID must match the platform post ID")
        return self

    def handoff(self, seen: set[str] | None = None) -> SocialEvidence:
        seen = seen if seen is not None else set()
        comments = []
        for comment in self.comments:
            key = keyword_key(comment.content)
            if key in seen:
                continue
            seen.add(key)
            label = "回复" if comment.parent_comment_id else "评论"
            comments.append(f"{label}：{comment.content}")
            if len(comments) == 6:
                break
        attribution = ""
        if self.provider == "zhihu_open_api":
            scope = "摘要" if self.content_scope == "excerpt" else "详情摘录"
            attribution = f"，来源 知乎开放平台{scope}"
            if self.author_display_name:
                attribution += f"，来源作者 {self.author_display_name}"
        return SocialEvidence(
            platform=self.platform,
            post_id=self.post_id,
            provider=self.provider,
            sample_size=len(comments),
            evidence={
                "evidence_id": self.evidence_id,
                "source_type": "social",
                "source_title": self.title,
                "source_url": self.original_url,
                "source_locator": (
                    f"{PLATFORM_NAMES[self.platform]}，1 篇原帖，{len(comments)} 条评论或回复摘录，"
                    f"采集时间 {self.collected_at.astimezone(UTC).isoformat()}，"
                    f"采集状态 {self.collection_status}{attribution}"
                ),
                "published_at": self.published_at,
                "retrieved_at": self.collected_at,
                "excerpt": "\n".join([self.content, *comments]),
            },
        )

    def to_evidence(self, seen: set[str] | None = None) -> Evidence:
        return Evidence.model_validate(self.handoff(seen).evidence.model_dump())


class ImportRequest(Contract):
    batch_id: UUID
    platform: Platform
    keyword: Keyword
    collected_at: AwareDatetime
    format: Literal["mediacrawler", "normalized"] = "mediacrawler"

    @model_validator(mode="after")
    def validate_time(self):
        if self.collected_at > datetime.now(UTC):
            raise ValueError("collected_at must reflect an actual collection time")
        return self


class SocialPage(Contract):
    total: Count
    items: list[SocialEvidenceRecord] = Field(max_length=MAX_POSTS)
