from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl

from travelmind.schemas.domain import Contract, Evidence, ShortText, SourceType, Text
from travelmind.schemas.phase4_intake import (
    ContractKey,
    Destination,
    Gcj02Point,
    NonnegativeInt,
    TravelMode,
)


class ResearchSourceRead(Contract):
    task_id: UUID
    source_id: UUID
    source_type: SourceType
    title: ShortText
    publisher: ShortText | None = None
    source_url: HttpUrl | None = None
    body_text: Annotated[Text, Field(max_length=50000)] | None = None


class TravelRoute(Contract):
    route_id: UUID
    provider: Literal["amap"] = "amap"
    mode: TravelMode
    origin: Destination
    destination: Destination
    fetched_at: AwareDatetime
    distance_m: NonnegativeInt
    duration_seconds: NonnegativeInt
    polyline: list[Gcj02Point] | None = Field(default=None, min_length=2)


class TravelRoutesRead(Contract):
    task_id: UUID
    routes: list[TravelRoute]


class SocialEvidenceItem(Evidence):
    source_type: Literal["social"] = "social"


SocialPlatform = Literal["xiaohongshu", "bilibili", "weibo", "douyin", "zhihu"]


class SocialEvidence(Contract):
    platform: SocialPlatform
    provider: ContractKey
    post_id: ShortText
    sample_size: NonnegativeInt | None = None
    evidence: SocialEvidenceItem
