import re
from collections import defaultdict
from datetime import UTC

from travelmind.research.social.schemas import PLATFORM_NAMES, keyword_key
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Contract, Evidence, Finding, ReportDraft

SOCIAL_SCOPE = (
    "社交资料的 post 与已清洗评论是便利样本。注明平台、实际入选原帖及评论条数、采集时间。"
    "区分评论样本数与平台显示的总评论数。避免用有限样本估计全体游客的观点、比例或满意度。"
    "访问受限或没有样本的渠道作为研究缺口。"
    "知乎开放平台搜索和问题回答接口提供摘要，引用时按摘要范围判断。"
)
SOCIAL_SKILLS = (
    "苏听雨使用五项技能，每项输出一个可引用的 Finding。"
    "travel_sentiment 说明样本中的正负体验和满意度线索。"
    "topic_clustering 按排队、预约、交通、消费、服务、商业化等可见主题归并。"
    "complaint_mining 优先呈现反复出现且有明确情境的问题及可核实的避坑线索。"
    "marketing_detection 指出广告、导流或重复文案迹象，疑似营销保留为待核；"
    "无迹象的帖子仍可能无法验证发布者身份。"
    "experience_synthesis 归纳体验差异，按季节、时段及访问条件解释分歧。"
    "单个经验可作为个案；原帖或评论不足以支撑该项时，标记 insufficient。"
)
TOPICS = (
    "排队",
    "预约",
    "交通",
    "消费",
    "服务",
    "商业化",
    "旺季",
    "淡季",
    "周末",
    "节假日",
    "门票",
    "亲子",
    "无障碍",
    "住宿",
)
SAMPLE = re.compile(r"^(.+?)，1 篇原帖，([0-6]) 条评论或回复摘录，")


class SocialSkillsResult(Contract):
    travel_sentiment: Finding
    topic_clustering: Finding
    complaint_mining: Finding
    marketing_detection: Finding
    experience_synthesis: Finding

    def findings(self) -> list[Finding]:
        named = (
            ("旅游体验情感", self.travel_sentiment),
            ("评价主题", self.topic_clustering),
            ("问题与投诉", self.complaint_mining),
            ("营销迹象", self.marketing_detection),
            ("体验综合", self.experience_synthesis),
        )
        return [
            finding.model_copy(update={"statement": f"{label}：{finding.statement}"})
            for label, finding in named
        ]


def social_keywords(request: AgentInput) -> list[str]:
    brief = request.brief
    focus = f"{brief.requirements} {request.assignment.objective if request.assignment else ''}"
    topics = brief.topics[:2] or [topic for topic in TOPICS if topic in focus][:2]
    destination = keyword_key(brief.destination)[:120].strip()
    primary = keyword_key(" ".join((destination, *topics)))[:120].strip()
    return list(dict.fromkeys([primary, destination]))


def with_social_sample_notes(draft: ReportDraft, evidence: list[Evidence]) -> ReportDraft:
    by_id = {item.evidence_id: item for item in evidence if item.source_type == "social"}
    if not by_id:
        return draft
    sections = []
    for section in draft.sections:
        samples: dict[str, list[tuple[str, int]]] = defaultdict(list)
        zhihu_authors: dict[str, None] = {}
        for key in dict.fromkeys(section.evidence_ids):
            item = by_id.get(key)
            if item and (match := SAMPLE.match(item.source_locator or "")):
                platform, count = match.groups()
                if platform in PLATFORM_NAMES.values():
                    stamp = item.retrieved_at.astimezone(UTC).isoformat(timespec="seconds")
                    samples[platform].append((stamp, int(count)))
                    if platform == "知乎":
                        _, _, author = (item.source_locator or "").partition("，来源作者 ")
                        if author:
                            zhihu_authors[author] = None
        if samples:
            notes = []
            for platform, rows in samples.items():
                dates = "、".join(sorted({stamp for stamp, _ in rows}))
                notes.append(
                    f"{platform} {len(rows)} 篇原帖、{sum(n for _, n in rows)} 条评论或回复，"
                    f"采集时间 {dates}"
                )
                if platform == "知乎" and zhihu_authors:
                    notes[-1] += f"，来源作者 {'、'.join(zhihu_authors)}"
            section = section.model_copy(
                update={
                    "body": f"{section.body}\n\n社交样本（UTC）  {'。'.join(notes)}。"
                    "以上仅反映本节引用的样本范围。",
                }
            )
        sections.append(section)
    return draft.model_copy(update={"sections": sections})
