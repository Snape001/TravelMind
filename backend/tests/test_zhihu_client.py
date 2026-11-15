import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from travelmind.research.social import zhihu_client
from travelmind.research.social.zhihu_client import ZhihuError, ZhihuOfficialClient


@pytest.fixture
def transport(monkeypatch):
    class Opener:
        replies = []
        requests = []

        def open(self, request, *, timeout):
            self.requests.append((request, timeout))
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            return BytesIO(raw)

    opener = Opener()
    monkeypatch.setattr(zhihu_client, "build_opener", lambda *handlers: opener)
    return opener


def test_search_bearer_timestamp_and_empty_result(transport):
    transport.replies = [{"Code": 0, "Data": {"HasMore": False, "Items": []}}]
    client = ZhihuOfficialClient(" local-test-secret ", timeout=3, min_interval=0)
    assert client("content/zhihu_search", Query="泉州", Count=1)["Items"] == []
    ((request, timeout),) = transport.requests
    url = urlsplit(request.full_url)
    assert url.netloc == "developer.zhihu.com"
    assert url.path == "/api/v1/content/zhihu_search"
    assert parse_qs(url.query) == {"Query": ["泉州"], "Count": ["1"]}
    assert request.get_header("Authorization") == "Bearer local-test-secret"
    assert request.get_header("X-request-timestamp").isdigit()
    assert timeout == 3


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (401, "AUTHENTICATION_FAILED"),
        (403, "ACCESS_DENIED"),
        (429, "RATE_LIMITED"),
        ({"Code": 20001}, "AUTHENTICATION_FAILED"),
        ({"Code": 30001}, "RATE_LIMITED"),
        ({"Code": 30002}, "QUOTA_EXHAUSTED"),
        ({"Code": 30003}, "ACCESS_DENIED"),
        ({"Code": 0, "Data": []}, "INVALID_RESPONSE"),
        ({"Code": True, "Data": {}}, "INVALID_RESPONSE"),
        (b"wrong payload", "INVALID_RESPONSE"),
        (TimeoutError(), "TIMEOUT"),
    ],
)
def test_permission_quota_and_malformed_failures_are_not_retried(transport, reply, expected):
    if type(reply) is int:
        reply = HTTPError("https://developer.zhihu.com", reply, "no", {}, BytesIO(b""))
    transport.replies = [reply]
    client = ZhihuOfficialClient("test", min_interval=0)
    with pytest.raises(ZhihuError, match=expected):
        client("content/zhihu_search", Query="泉州")
    assert len(transport.requests) == 1


def test_retries_one_transient_failure_and_paces_requests(transport, monkeypatch):
    elapsed = 0.0
    delays = []

    def sleep(seconds):
        nonlocal elapsed
        delays.append(seconds)
        elapsed += seconds

    monkeypatch.setattr(zhihu_client, "monotonic", lambda: elapsed)
    monkeypatch.setattr(zhihu_client, "sleep", sleep)
    transport.replies = [{"Code": 90001}, {"Code": 0, "Data": {"Items": []}}]
    client = ZhihuOfficialClient("test")
    assert client("content/zhihu_search", Query="泉州") == {"Items": []}
    assert len(transport.requests) == 2 and delays == [1.0]

    transport.replies = [{"Code": 90001}, {"Code": 90001}]
    with pytest.raises(ZhihuError, match="UPSTREAM_UNAVAILABLE"):
        client("content/zhihu_search", Query="泉州")
    assert len(transport.requests) == 4


def test_quota_uses_only_official_capability_ids(transport):
    item = {"APIID": "zhihu_search", "RemainingQuota": 4}
    transport.replies = [{"Code": 0, "Data": [item]}]
    client = ZhihuOfficialClient("test", min_interval=0)
    assert client.quota() == [item]
    ((request, _),) = transport.requests
    assert urlsplit(request.full_url).path == "/api/v1/quota"
    assert parse_qs(urlsplit(request.full_url).query)["APIIDs"] == [
        "zhihu_search,question_discovery,creator",
    ]


def test_arbitrary_endpoints_cannot_receive_the_credential(transport):
    client = ZhihuOfficialClient("test")
    with pytest.raises(ValueError, match="unsupported Zhihu endpoint"):
        client("unlisted/path")
    assert not transport.requests
