import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from travelmind.research.sources import DOI_HOSTS, is_public_target, source_key, web_source_type
from travelmind.schemas.domain import Evidence
from travelmind.schemas.research_quality import QualityScore

DOI = re.compile(r"10\.[0-9]{4,9}/\S+")
ISBN = re.compile(r"ISBN(?:[- ]?(?:10|13))?\s*:?\s*([0-9Xx -]+)", re.IGNORECASE)
PARTS = re.compile(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+")
HAN = re.compile(r"[\u3400-\u9fff]+")
STOP_WORDS = frozenset(
    "the a an to is are in of and for on with how what when where about "
    "请问 查询 调研 介绍 了解 根据 有关 是否 哪些 如何 什么 时候 情况".split()
)
MARKDOWN_LINK = re.compile(r"!?\[([^\]\n]*)\]\([^\n)]*\)")
PLACEHOLDERS = frozenset(
    {"暂无正文", "暂无内容", "点击查看详情", "请登录后查看", "readmore", "nocontent"}
)
ORIGIN_SIGNAL_LABELS = {
    "doi": "DOI",
    "isbn": "ISBN",
    "url": "规范化 URL",
    "host": "发布主机",
    "origin": "原始发布标识",
    "excerpt": "摘录正文",
}


def bibliographic_id(item: Evidence) -> tuple[str, str] | None:
    kind, value = source_key(item)
    if kind == "doi" and DOI.fullmatch(value):
        return kind, value
    locator = unicodedata.normalize("NFKC", item.source_locator or "").casefold()
    doi = locator.removeprefix("doi:").strip()
    if DOI.fullmatch(doi):
        return "doi", doi
    match = ISBN.fullmatch(locator)
    if match is None:
        return None
    digits = re.sub(r"[- ]", "", match[1])
    if len(digits) == 10 and re.fullmatch(r"[0-9]{9}[0-9x]", digits):
        checksum = sum((10 - i) * int(char) for i, char in enumerate(digits[:9]))
        if (checksum + (10 if digits[-1] == "x" else int(digits[-1]))) % 11 == 0:
            prefix = "978" + digits[:9]
            check = -sum((1, 3)[i % 2] * int(char) for i, char in enumerate(prefix)) % 10
            return "isbn", f"{prefix}{check}"
    if re.fullmatch(r"97[89][0-9]{10}", digits):
        if sum((1, 3)[i % 2] * int(char) for i, char in enumerate(digits)) % 10 == 0:
            return "isbn", digits
    return None


def provenance(item: Evidence) -> tuple[QualityScore, QualityScore]:
    url = item.source_url
    identifier = bibliographic_id(item)
    if url is not None and not is_public_target(url):
        return (
            QualityScore(score=0, reason="source_url 未通过公开目标检查"),
            QualityScore(score=0, reason="不接受不可追溯 URL 的发布方线索"),
        )
    if url is not None:
        traceability = QualityScore(score=2, reason="source_url 通过既有公开目标检查")
    elif identifier is not None:
        traceability = QualityScore(
            score=2, reason=f"source_locator 提供标准 {identifier[0].upper()}"
        )
    else:
        traceability = QualityScore(score=1, reason="source_locator 有非标准书目定位符")

    if url is not None and web_source_type(url) == "official":
        authority = QualityScore(score=2, reason="source_url 命中现有 gov.cn 政务域规则")
    elif identifier is not None and identifier[0] == "doi":
        authority = QualityScore(score=2, reason="归一化来源标识符合 DOI 格式")
    else:
        authority = QualityScore(score=1, reason="资料有定位信息，发布方权威性尚未核实")
    return traceability, authority


def freshness(item: Evidence, as_of: datetime, max_age_days: int | None) -> QualityScore:
    published = item.published_at
    if published is not None:
        age = as_of - published.astimezone(UTC)
        if age < timedelta(0):
            return QualityScore(score=0, reason="published_at 晚于评分参考时间")
        if max_age_days is not None and max_age_days <= age.days:
            if age > timedelta(days=max_age_days):
                return QualityScore(score=0, reason=f"published_at 超过 {max_age_days} 天时效窗口")
    if max_age_days is None:
        return QualityScore(score=2, reason="分工无时效窗口，published_at 无未来日期")
    if published is None:
        return QualityScore(score=1, reason="分工要求时效，但缺少 published_at")
    return QualityScore(score=2, reason=f"published_at 在 {max_age_days} 天时效窗口内")


def plain_text(text: str) -> str:
    return unicodedata.normalize("NFKC", MARKDOWN_LINK.sub(r"\1", text)).casefold()


def compact(text: str) -> str:
    return "".join(char for char in plain_text(text) if char.isalnum())


def terms(text: str) -> set[str]:
    # CJK text usually lacks word boundaries. Adjacent characters preserve more context
    # than testing individual characters while still matching ordinary Chinese queries.
    result = set()
    for match in PARTS.finditer(plain_text(text)):
        part = match[0]
        if HAN.fullmatch(part):
            result.update(part[i : i + 2] for i in range(max(1, len(part) - 1)))
        else:
            result.add(part)
    return result - STOP_WORDS


def body_text(item: Evidence) -> str:
    return "\n".join(
        line for line in plain_text(item.excerpt).splitlines() if not line.lstrip().startswith("#")
    )


def completeness(item: Evidence, body: str) -> QualityScore:
    normalized = compact(body)
    if not normalized or normalized in PLACEHOLDERS or normalized == compact(item.source_title):
        return QualityScore(score=0, reason="excerpt 只有标题、占位内容或无可读正文")
    if len(normalized) >= 24 and len(terms(body)) >= 12:
        return QualityScore(score=2, reason="excerpt 包含达到正文长度与词项要求的可读材料")
    return QualityScore(score=1, reason="excerpt 有可读内容，但长度或词项不足")


def relevance(objective_terms: set[str], body: str, retrieval_score: float | None) -> QualityScore:
    matches = objective_terms & terms(body)
    if matches and 5 * len(matches) >= 4 * len(objective_terms):
        return QualityScore(
            score=2, reason=f"excerpt 命中 {len(matches)}/{len(objective_terms)} 个目标词项"
        )
    if matches:
        return QualityScore(
            score=1, reason=f"excerpt 仅命中 {len(matches)}/{len(objective_terms)} 个目标词项"
        )
    if retrieval_score is not None and retrieval_score >= 0.75 and compact(body):
        return QualityScore(
            score=1, reason=f"检索归一化分数 {retrieval_score:.2f} 提示可能相关，正文待核对"
        )
    return QualityScore(score=0, reason="excerpt 无目标词项命中或可用的强检索信号")


def origin_signals(item: Evidence, verified_origin: str | None) -> set[tuple[str, str]]:
    identity = {source_key(item)}
    if identifier := bibliographic_id(item):
        identity.add(identifier)
    if item.source_url is not None:
        host = (item.source_url.host or "").rstrip(".").lower().removeprefix("www.")
        if host and host not in DOI_HOSTS and host != "openalex.org":
            identity.add(("host", host))
    if verified_origin:
        identity.add(("origin", verified_origin))
    if len(excerpt := compact(item.excerpt)) >= 24:
        identity.add(("excerpt", excerpt))
    return identity


def independence(
    items: Sequence[Evidence], origins: Mapping[UUID, str], eligible_ids: set[UUID]
) -> list[QualityScore]:
    signals = [origin_signals(item, origins.get(item.evidence_id)) for item in items]
    counts = Counter(signal for identity in signals for signal in identity)
    shared = [
        {ORIGIN_SIGNAL_LABELS.get(key[0], "书目定位符") for key in identity if counts[key] > 1}
        for identity in signals
    ]
    verified = {
        origins[item.evidence_id]
        for item, same_source in zip(items, shared, strict=True)
        if not same_source and item.evidence_id in eligible_ids and origins.get(item.evidence_id)
    }
    scores = []
    for item, matching_signals in zip(items, shared, strict=True):
        if matching_signals:
            labels = "、".join(sorted(matching_signals))
            score = QualityScore(score=0, reason=f"批次内有相同的{labels}")
        elif (
            item.evidence_id in eligible_ids
            and len(verified) >= 2
            and origins.get(item.evidence_id)
        ):
            score = QualityScore(score=2, reason="批次中存在至少两个已核实的不同发布源")
        else:
            score = QualityScore(score=1, reason="独立性尚未确认，按一份来源处理")
        scores.append(score)
    return scores
