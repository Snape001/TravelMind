import json
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import HttpUrl

from travelmind.research.social import zhihu_api
from travelmind.research.social.platforms import load_export
from travelmind.research.social.schemas import (
    ImportRequest,
    SocialEvidenceRecord,
    social_evidence_id,
)
from travelmind.research.social.zhihu_api import ZhihuError, collect
from travelmind.research.sources import source_key
from travelmind.schemas.phase4_openapi import phase4_components

COLLECTED = datetime(2025, 11, 14, tzinfo=UTC)
QUESTION = "https://www.zhihu.com/question/123"
ANSWER = f"{QUESTION}/answer/456"


def write_rows(path, *rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def test_zhihu_authorized_export_preserves_question_and_answer_comments(tmp_path):
    question = {"content_id": "123", "content_type": "question", "title": "泉州怎么玩"}
    answer = {
        "content_id": "456",
        "question_id": "123",
        "content_type": "answer",
        "content_url": ANSWER + "?token=hidden",
        "content_text": "<p>周末建议预约。</p>",
        "creator_hash": "private",
    }
    write_rows(tmp_path / "contents.jsonl", question, answer, answer)
    first = {
        "comment_id": "10",
        "content": "周末拥挤",
        "content_id": "456",
        "content_type": "answer",
    }
    second = {**first, "comment_id": "11", "parent_comment_id": "10", "content": "工作日人少"}
    unrelated = {**first, "content_type": "question"}
    write_rows(tmp_path / "comments.jsonl", first, second, first, unrelated)
    request = ImportRequest(
        batch_id=uuid4(), platform="zhihu", keyword="泉州 预约", collected_at=COLLECTED
    )
    (question, answer), errors = load_export(tmp_path, request)
    assert errors == 0
    assert str(question.original_url) == QUESTION and question.comments == []
    assert question.collection_status == "partial"
    assert str(answer.original_url) == ANSWER and answer.post_id == "answer-456"
    assert answer.comments[1].parent_comment_id == "10" and len(answer.comments) == 2
    assert "private" not in answer.model_dump_json() and "token=" not in answer.model_dump_json()
    assert answer.handoff().platform == "zhihu"
    assert "知乎" in answer.to_evidence().source_locator


def send(routes):
    calls = []

    def call(endpoint, **params):
        calls.append((endpoint, params))
        return next(routes)

    return call, calls


def test_manual_browser_records_preserve_sample_origin(tmp_path):
    request = ImportRequest(
        batch_id=uuid4(),
        platform="zhihu",
        keyword="泉州",
        collected_at=COLLECTED,
        format="normalized",
    )
    payload = {
        "evidence_id": social_evidence_id("zhihu", "answer-456", ANSWER),
        "platform": "zhihu",
        "post_id": "answer-456",
        "content_type": "answer",
        "provider": "manual_browser",
        "content_scope": "excerpt",
        "title": "合成样例",
        "content": "合成回答摘录",
        "original_url": ANSWER,
        "keyword": request.keyword,
        "collected_at": COLLECTED,
        "collection_status": "partial",
        "comments": [
            {"comment_id": "local-1", "content": "合成评论"},
            {"comment_id": "local-2", "parent_comment_id": "local-1", "content": "合成回复"},
        ],
    }
    evidence = SocialEvidenceRecord.model_validate(payload)
    (tmp_path / "evidence.jsonl").write_text(evidence.model_dump_json() + "\n", encoding="utf-8")
    (loaded,), errors = load_export(tmp_path, request)
    assert errors == 0 and loaded == evidence
    assert loaded.handoff().provider == "manual_browser"
    assert "人工浏览摘录" in loaded.to_evidence().source_locator
    assert "回复：合成回复" in loaded.to_evidence().excerpt
    with pytest.raises(ValueError, match="manual browser"):
        SocialEvidenceRecord.model_validate({**payload, "collection_status": "collected"})


def test_official_search_keeps_attribution_and_selected_comment_scope():
    item = {
        "Title": "泉州",
        "ContentType": "Answer",
        "ContentID": "-9001",
        "ContentText": "周末建议预约",
        "Url": ANSWER + "?utm_medium=openapi_platform&utm_source=abc&session=discard",
        "CommentCount": 20,
        "AuthorName": "公开昵称",
        "CreateTime": 1760000000,
        "EditTime": 1760000123,
        "CommentInfoList": [{"Content": "周末需排队"}, {"Content": "周末需排队"}],
    }
    call, calls = send(iter([{"Items": [item]}]))
    (record,) = collect(call, "search", "泉州", COLLECTED)
    assert calls == [("content/zhihu_search", {"Query": "泉州", "Count": 10})]
    assert record.provider == "zhihu_open_api"
    assert record.post_id == "answer-456"
    assert record.author_display_name == "公开昵称"
    assert record.created_at == datetime.fromtimestamp(1760000000, UTC)
    assert record.published_at is None
    assert record.collected_at == COLLECTED
    assert "utm_source=abc" in str(record.original_url)
    assert "session=" not in str(record.original_url)
    assert record.collection_status == "partial" and len(record.comments) == 1
    assert record.comments[0].comment_id.startswith("excerpt-")
    evidence = record.to_evidence()
    short_url = evidence.model_copy(
        update={"source_url": HttpUrl("https://www.zhihu.com/answer/456")}
    )
    assert source_key(evidence) == source_key(short_url)


def test_search_question_identity_comes_from_its_public_url():
    item = {
        "ContentType": "Question",
        "ContentID": "9001",
        "Title": "泉州",
        "Url": QUESTION,
    }
    answer_with_wrong_url = {**item, "ContentType": "Answer"}
    call, _ = send(iter([{"Items": [item]}, {"Items": [answer_with_wrong_url]}]))
    (record,) = collect(call, "search", "泉州", COLLECTED)
    assert record.post_id == "question-123"
    with pytest.raises(ZhihuError, match="INVALID_RESPONSE"):
        collect(call, "search", "泉州", COLLECTED)


def test_question_answers_follows_server_offset_with_two_page_budget():
    first = {"Items": [], "Paging": {"IsEnd": False, "NextOffset": 17}}
    second = {
        "Items": [{"ContentToken": "456", "Url": ANSWER, "Summary": "泉州体验"}],
        "Paging": {"IsEnd": False, "NextOffset": 99},
    }
    call, calls = send(iter([first, second]))
    (record,) = collect(call, "answers", "泉州", COLLECTED, QUESTION)
    assert [params["Offset"] for _, params in calls] == [0, 17]
    assert record.post_id == "answer-456" and record.collection_status == "partial"


def test_question_answers_rejects_nonincreasing_offset():
    page = {"Items": [], "Paging": {"IsEnd": False, "NextOffset": 0}}
    call, calls = send(iter([page]))
    with pytest.raises(ZhihuError, match="INVALID_PAGINATION"):
        collect(call, "answers", "泉州", COLLECTED, QUESTION)
    assert len(calls) == 1


def test_owned_answer_reads_child_replies_and_rejects_wrong_target(tmp_path):
    owned = {"ContentType": "answer", "ContentToken": "456", "Url": ANSWER, "Body": "详情"}
    comments = {
        "Items": [
            {
                "Comment": {"ID": 19_446_744_073_709_551_615, "Content": "原评论"},
                "Children": [
                    {"ID": "9", "Content": "回复", "CreatedAt": 1760000123},
                    {
                        "ID": "10",
                        "Content": "回复",
                        "ReplyID": "9",
                        "RootID": 19_446_744_073_709_551_615,
                        "AuthorToken": "local-test-author",
                    },
                ],
            }
        ],
        "Paging": {"IsEnd": True},
    }
    call, calls = send(iter([owned, comments]))
    (record,) = collect(call, "owned", "泉州", COLLECTED, ANSWER)
    assert [name for name, _ in calls] == ["user/content_detail", "user/content_comments"]
    assert record.comments[1].parent_comment_id == record.comments[0].comment_id
    assert record.comments[0].comment_id == "19446744073709551615"
    assert record.comments[1].published_at == datetime.fromtimestamp(1760000123, UTC)
    assert record.comments[2].parent_comment_id == "9"
    assert record.comments[2].root_comment_id == "19446744073709551615"
    assert record.comments[2].author_token == "local-test-author"
    assert record.collection_status == "partial"
    assert "local-test-author" not in record.to_evidence().model_dump_json()

    request = ImportRequest(
        batch_id=uuid4(),
        platform="zhihu",
        keyword="泉州",
        collected_at=COLLECTED,
        format="normalized",
    )
    (tmp_path / "evidence.jsonl").write_text(record.model_dump_json() + "\n", encoding="utf-8")
    (loaded,), errors = load_export(tmp_path, request)
    assert errors == 0 and loaded == record
    call, _ = send(iter([{**owned, "Url": ANSWER.replace("456", "457")}]))
    with pytest.raises(ZhihuError, match="INVALID_RESPONSE"):
        collect(call, "owned", "泉州", COLLECTED, ANSWER)


def test_search_distinguishes_empty_from_malformed_rows():
    call, _ = send(iter([{"Items": []}, {"Items": ["not a record"]}]))
    assert collect(call, "search", "泉州", COLLECTED) == []
    with pytest.raises(ZhihuError, match="INVALID_RESPONSE"):
        collect(call, "search", "泉州", COLLECTED)


def test_cli_import_receipt_transports_chinese_keyword_as_ascii_json(tmp_path, monkeypatch, capsys):
    keyword = "泉州古城 预约 排队"

    def search(endpoint, **params):
        assert endpoint == "content/zhihu_search" and params["Query"] == keyword
        return {"Items": []}

    monkeypatch.setattr(zhihu_api.ZhihuOfficialClient, "from_environment", lambda: search)
    monkeypatch.setattr(sys, "argv", ["zhihu_api", "--data", str(tmp_path), "--keyword", keyword])
    zhihu_api.main()
    receipt = capsys.readouterr().out
    assert receipt.isascii()
    assert ImportRequest.model_validate_json(receipt).keyword == keyword


def test_phase4_contract_advertises_zhihu():
    fields = phase4_components()["components"]["schemas"]["SocialEvidence"]["properties"]
    assert "zhihu" in fields["platform"]["enum"]
