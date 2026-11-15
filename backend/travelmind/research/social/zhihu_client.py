import json
import os
from http.client import HTTPException
from time import monotonic, sleep, time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ORIGIN = "https://developer.zhihu.com/api/v1/"
CONTENT_ENDPOINTS = frozenset(
    {
        "content/zhihu_search",
        "content/question_answers",
        "user/content_detail",
        "user/content_comments",
    }
)
MAX_BYTES = 512 * 1024
API_ERRORS = {
    10001: "CONTENT_UNAVAILABLE",
    20001: "AUTHENTICATION_FAILED",
    30001: "RATE_LIMITED",
    30002: "QUOTA_EXHAUSTED",
    30003: "ACCESS_DENIED",
    90001: "UPSTREAM_UNAVAILABLE",
}
HTTP_ERRORS = {
    401: "AUTHENTICATION_FAILED",
    403: "ACCESS_DENIED",
    404: "CONTENT_UNAVAILABLE",
    429: "RATE_LIMITED",
    502: "UPSTREAM_UNAVAILABLE",
    503: "UPSTREAM_UNAVAILABLE",
    504: "UPSTREAM_UNAVAILABLE",
}


class ZhihuError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ZhihuOfficialClient:
    """Sequential client for the documented read APIs on developer.zhihu.com."""

    def __init__(self, secret: str, *, timeout: float = 8, min_interval: float = 1):
        self._secret = secret.strip()
        if not self._secret:
            raise ZhihuError("ZHIHU_ACCESS_SECRET_REQUIRED")
        if not 0.5 <= timeout <= 30 or not 0 <= min_interval <= 10:
            raise ZhihuError("INVALID_CLIENT_CONFIG")
        self.timeout = timeout
        self.min_interval = min_interval
        self._next_request = 0.0
        self._opener = build_opener(NoRedirect())

    @classmethod
    def from_environment(cls):
        try:
            timeout = float(os.environ.get("ZHIHU_TIMEOUT_SECONDS", "8"))
            interval = float(os.environ.get("ZHIHU_MIN_INTERVAL_SECONDS", "1"))
        except ValueError:
            raise ZhihuError("INVALID_CLIENT_CONFIG") from None
        return cls(
            os.environ.get("ZHIHU_ACCESS_SECRET", ""), timeout=timeout, min_interval=interval
        )

    def __call__(self, endpoint: str, **params: str | int) -> dict:
        if endpoint not in CONTENT_ENDPOINTS:
            raise ValueError("unsupported Zhihu endpoint")
        data = self._request(endpoint, params)
        if not isinstance(data, dict):
            raise ZhihuError("INVALID_RESPONSE")
        return data

    def quota(self) -> list[dict]:
        data = self._request("quota", {"APIIDs": "zhihu_search,question_discovery,creator"})
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ZhihuError("INVALID_RESPONSE")
        return data

    def _request(self, endpoint: str, params: dict[str, str | int]):
        url = f"{API_ORIGIN}{endpoint}?{urlencode(params)}"
        for attempt in range(2):
            delay = self._next_request - monotonic()
            if delay > 0:
                sleep(delay)
            self._next_request = monotonic() + self.min_interval
            request = Request(
                url,
                headers={
                    "Authorization": f"Bearer {self._secret}",
                    "X-Request-Timestamp": str(int(time())),
                    "Content-Type": "application/json",
                },
            )
            try:
                return self._read(request)
            except ZhihuError as exc:
                if attempt or str(exc) != "UPSTREAM_UNAVAILABLE":
                    raise
        raise AssertionError("at least one request attempt is required")

    def _read(self, request: Request):
        deadline = monotonic() + self.timeout
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = bytearray()
                while chunk := response.read1(32 * 1024):
                    if monotonic() > deadline:
                        raise ZhihuError("TIMEOUT")
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ZhihuError("RESPONSE_TOO_LARGE")
            if monotonic() > deadline:
                raise ZhihuError("TIMEOUT")
            payload = json.loads(raw)
        except HTTPError as exc:
            code = HTTP_ERRORS.get(exc.code, "HTTP_ERROR")
            exc.close()
            raise ZhihuError(code) from None
        except TimeoutError:
            raise ZhihuError("TIMEOUT") from None
        except URLError as exc:
            code = "TIMEOUT" if isinstance(exc.reason, TimeoutError) else "NETWORK_ERROR"
            raise ZhihuError(code) from None
        except (OSError, HTTPException):
            raise ZhihuError("NETWORK_ERROR") from None
        except (ValueError, RecursionError):
            raise ZhihuError("INVALID_RESPONSE") from None

        if not isinstance(payload, dict) or type(payload.get("Code")) is not int:
            raise ZhihuError("INVALID_RESPONSE")
        if payload["Code"] != 0:
            raise ZhihuError(API_ERRORS.get(payload["Code"], "UPSTREAM_ERROR"))
        return payload.get("Data")
