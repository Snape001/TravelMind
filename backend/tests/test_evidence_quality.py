import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from travelmind.research.quality import EvidenceQualityScorer
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import Assignment, Evidence, ResearchBrief
from travelmind.schemas.research_quality import EvidenceQuality

AS_OF = datetime(2026, 11, 15, tzinfo=UTC)
EXCERPT = "泉州博物馆每周二至周日开放，开放时间为上午九点至下午五点，周一闭馆。"


@pytest.fixture
def request_input():
    return AgentInput(
        task_id=uuid4(),
        trace_id=uuid4(),
        run_id=uuid4(),
        agent_id="travel",
        brief=ResearchBrief(destination="泉州"),
        assignment=Assignment(assignment_id=uuid4(), agent_id="travel", objective="泉州博物馆开放"),
        budget=RunBudget(max_attempts=1),
    )


def evidence(**fields):
    return Evidence.model_validate(
        {
            "evidence_id": uuid4(),
            "source_type": "official",
            "source_title": "泉州博物馆参观公告",
            "source_url": "https://www.quanzhou.gov.cn/museum",
            "retrieved_at": AS_OF,
            "published_at": AS_OF - timedelta(days=2),
            "excerpt": EXCERPT,
        }
        | fields
    )


def assess(request_input, *items, scorer=None, **options):
    scorer = scorer or EvidenceQualityScorer()
    return asyncio.run(
        scorer.assess(
            request_input,
            items,
            **({"as_of": AS_OF, "max_age_days": None} | options),
        )
    )


@pytest.mark.parametrize(
    ("source_type", "url", "locator", "authority", "score", "grade"),
    [
        ("official", "https://www.quanzhou.gov.cn/museum", None, 2, 97, "A"),
        ("academic", None, "DOI:10.1234/tourism", 2, 94, "A"),
        ("book", None, "ISBN 978-0-306-40615-7", 1, 81, "B"),
        ("web", "https://travel.example.com/museum", None, 1, 83, "B"),
        ("social", "https://community.example.org/post/1", None, 1, 82, "B"),
        ("map", "https://maps.example.net/site/1", None, 1, 88, "B"),
    ],
)
def test_source_weights_and_original_provenance(
    request_input, source_type, url, locator, authority, score, grade
):
    item = evidence(source_type=source_type, source_url=url, source_locator=locator)
    before = item.model_dump_json()
    [result] = assess(request_input, item)
    assert result.evidence is item
    assert result.evidence.model_dump_json() == before
    assert result.assignment_id == request_input.assignment.assignment_id
    assert result.authority.score == authority
    assert result.score == score
    assert result.grade == grade
    assert result.action == ("use" if grade == "A" else "cross_verify")
    assert result.quality is None


@pytest.mark.parametrize(
    ("published", "window", "score", "action"),
    [
        (AS_OF - timedelta(days=30), 30, 2, "use"),
        ((AS_OF - timedelta(days=30)).astimezone(timezone(timedelta(hours=8))), 30, 2, "use"),
        (AS_OF - timedelta(days=30, microseconds=1), 30, 0, "exclude"),
        (AS_OF + timedelta(microseconds=1), None, 0, "exclude"),
        (None, 30, 1, "supplement_search"),
        (None, None, 2, "use"),
    ],
)
def test_publication_window(request_input, published, window, score, action):
    [result] = assess(
        request_input, evidence(published_at=published, retrieved_at=AS_OF), max_age_days=window
    )
    assert result.freshness.score == score
    assert result.action == action
    if action == "supplement_search":
        assert result.target_question == request_input.assignment.objective


@pytest.mark.parametrize(
    ("url", "locator", "traceability", "authority"),
    [
        ("http://127.0.0.1/museum", "DOI:10.1234/tourism", 0, 0),
        ("https://quanzhou.gov.cn.example.com/museum", None, 2, 1),
        ("https://doi.org/10.fake/title", None, 2, 1),
        (None, "本地馆藏目录第 42 页", 1, 1),
        (None, "ISBN 978-0-306-40615-0", 1, 1),
    ],
)
def test_authority_requires_verifiable_origin(request_input, url, locator, traceability, authority):
    [result] = assess(request_input, evidence(source_url=url, source_locator=locator))
    assert result.traceability.score == traceability
    assert result.authority.score == authority
    if traceability == 0:
        assert result.action == "exclude"


def test_search_signal_is_optional_and_cannot_promote_title_only_evidence(request_input):
    title_only = evidence(source_title="泉州博物馆开放", excerpt="# 泉州博物馆开放")
    alternate_terms = evidence(excerpt="市区文物展厅每日上午九时开始接受参观，当日下午十七时清场。")
    scores = {title_only.evidence_id: 1.0, alternate_terms.evidence_id: 0.9}
    title_result, alternate_result = assess(
        request_input, title_only, alternate_terms, retrieval_scores=scores
    )
    assert title_result.completeness.score == 0
    assert title_result.action == "exclude"
    assert alternate_result.relevance.score == 1
    assert "0.90" in alternate_result.relevance.reason
    assert alternate_result.action == "supplement_search"
    [without_score] = assess(request_input, alternate_terms)
    assert without_score.relevance.score == 0
    with pytest.raises(ValueError, match="retrieval"):
        assess(request_input, alternate_terms, retrieval_scores={alternate_terms.evidence_id: 1.2})


def test_background_with_the_same_entity_needs_targeted_research(request_input):
    item = evidence(excerpt="泉州博物馆近期举办民俗展览，展品包括漆器、陶瓷和民间工艺品。")
    [result] = assess(request_input, item, retrieval_scores={item.evidence_id: 1.0})
    assert result.relevance.score == 1
    assert result.grade == "C"
    assert result.target_question == request_input.assignment.objective


@pytest.mark.parametrize(
    "variant",
    [
        {"source_url": "https://www.quanzhou.gov.cn/museum?utm_source=copy#text"},
        {"source_url": "https://quanzhou.gov.cn/other", "excerpt": EXCERPT + "节假日另行公告。"},
        {"source_url": "https://mirror.example.org/museum", "excerpt": EXCERPT.replace("，", " ")},
    ],
)
def test_same_source_and_obvious_reposts_do_not_corroborate(request_input, variant):
    first, second = evidence(), evidence(**variant)
    results = assess(
        request_input,
        first,
        second,
        verified_origins={first.evidence_id: "one", second.evidence_id: "two"},
    )
    assert [result.independence.score for result in results] == [0, 0]
    assert [result.action for result in results] == ["cross_verify", "cross_verify"]


def test_doi_locator_and_resolver_are_one_origin(request_input):
    url = evidence(source_url="https://doi.org/10.1234/a", source_type="academic")
    locator = evidence(
        source_url=None, source_locator="doi:10.1234/A", source_type="academic", excerpt=EXCERPT * 2
    )
    assert [r.independence.score for r in assess(request_input, url, locator)] == [0, 0]


def test_distinct_hosts_only_count_when_origins_are_verified(request_input):
    first = evidence()
    second = evidence(
        source_url="https://another.example.org/local",
        excerpt="泉州博物馆开放时段为早上九点至傍晚五点，闭馆前半小时停止入场。",
    )
    assert [r.independence.score for r in assess(request_input, first, second)] == [1, 1]
    assert [
        r.independence.score
        for r in assess(
            request_input,
            first,
            second,
            verified_origins={first.evidence_id: "public-notice", second.evidence_id: "on-site"},
        )
    ] == [2, 2]


class SemanticModel:
    def __init__(self, *, fabricated_quote=False):
        self.calls = []
        self.fabricated_quote = fabricated_quote

    async def __call__(self, request, instructions, response_type, *, context):
        assert request.evidence == request.prior_findings == []
        assert request.draft is None
        self.calls.append(context)
        return response_type.model_validate(
            {
                "assessments": [
                    {
                        "evidence_id": item["evidence_id"],
                        "relevance": {
                            "score": 2,
                            "reason": "覆盖开放问题",
                            "quote": item["excerpt"],
                        },
                        "directness": {
                            "score": 2,
                            "reason": "明确说明开放时段",
                            "quote": (
                                "原文中不存在的说法" if self.fabricated_quote else item["excerpt"]
                            ),
                        },
                    }
                    for item in reversed(context["evidence"])
                ]
            }
        )


def test_optional_model_batches_caches_and_emits_frozen_cards(request_input):
    model = SemanticModel()
    scorer = EvidenceQualityScorer(model=model, model_id="test-model")
    items = [
        evidence(),
        evidence(
            source_url="https://example.org/notice",
            excerpt="泉州市文物展厅每天早上九点开始接待。",
        ),
        evidence(source_url="https://example.net/times", excerpt="展厅接待时段是九时至十七时。"),
    ]
    request_input.evidence = items
    results = assess(request_input, *items, scorer=scorer)
    assert [len(call["evidence"]) for call in model.calls] == [2]
    assert results[0].quality is None
    assert results[1].relevance.score == 2
    cards = assess(request_input, *items, scorer=scorer, require_contract_cards=True)
    assert [len(call["evidence"]) for call in model.calls] == [2, 1]
    for item, result in zip(items, cards, strict=True):
        card = result.quality
        assert EvidenceQuality.model_validate_json(card.model_dump_json()) == card
        assert card.model_id == "test-model"
        assert card.evidence_id == item.evidence_id
        assert card.relevance.quote in item.excerpt
        assert card.directness.quote in item.excerpt
    assert assess(request_input, *items, scorer=scorer, require_contract_cards=True) == cards
    assert len(model.calls) == 2
    assess(request_input, *items, scorer=scorer, require_contract_cards=True, max_age_days=30)
    assert [len(call["evidence"]) for call in model.calls] == [2, 1, 3]
    changed = items[1].model_copy(update={"excerpt": "修订后的开放时间是上午十点。"})
    assess(request_input, changed, scorer=scorer, require_contract_cards=True)
    assert len(model.calls) == 4
    new_task = request_input.model_copy(update={"task_id": uuid4()})
    assess(new_task, items[0], scorer=scorer, require_contract_cards=True)
    assert len(model.calls) == 5


def test_invalid_model_quote_is_never_cached_or_reported_as_a_card(request_input):
    model = SemanticModel(fabricated_quote=True)
    scorer = EvidenceQualityScorer(model=model, model_id="test-model")
    item = evidence()
    for _ in range(2):
        [result] = assess(request_input, item, scorer=scorer, require_contract_cards=True)
        assert result.quality is None
        assert result.action == "use"
    assert len(model.calls) == 2


def test_invalid_quote_preserves_the_other_valid_card_in_the_batch(request_input):
    class MixedQuotes(SemanticModel):
        async def __call__(self, *args, **kwargs):
            result = await super().__call__(*args, **kwargs)
            result.assessments[0].directness.quote = "不存在的原文"
            return result

    model = MixedQuotes()
    scorer = EvidenceQualityScorer(model=model, model_id="test-model")
    first, second = evidence(), evidence(source_url="https://example.org/source")
    observed = assess(request_input, first, second, scorer=scorer, require_contract_cards=True)
    assert observed[0].quality is not None
    assert observed[1].quality is None
    assess(request_input, first, second, scorer=scorer, require_contract_cards=True)
    assert [len(call["evidence"]) for call in model.calls] == [2, 1]


def test_batch_limit_and_invalid_ids_do_not_discard_other_batches(request_input):
    class BatchModel(SemanticModel):
        async def __call__(self, *args, **kwargs):
            response = await super().__call__(*args, **kwargs)
            if len(self.calls) == 1:
                response.assessments[0].evidence_id = uuid4()
            return response

    model = BatchModel()
    scorer = EvidenceQualityScorer(model=model, model_id="test-model")
    items = [evidence(source_url=f"https://site{i}.example.org/notice") for i in range(5)]
    first = assess(request_input, *items, scorer=scorer, require_contract_cards=True)
    assert [len(call["evidence"]) for call in model.calls] == [4, 1]
    assert all(result.quality is None for result in first[:4])
    assert first[-1].quality.evidence_id == items[-1].evidence_id
    second = assess(request_input, *items, scorer=scorer, require_contract_cards=True)
    assert [len(call["evidence"]) for call in model.calls] == [4, 1, 4]
    assert all(result.quality is not None for result in second)


def test_rejects_ambiguous_context(request_input):
    item = evidence()
    with pytest.raises(ValueError, match="unique"):
        assess(request_input, item, item)
    with pytest.raises(ValueError, match="timezone"):
        assess(request_input, item, as_of=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValueError, match="max_age_days"):
        assess(request_input, item, max_age_days=-1)
    with pytest.raises(ValueError, match="model"):
        assess(request_input, item, require_contract_cards=True)
