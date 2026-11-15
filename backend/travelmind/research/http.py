import asyncio
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from json import loads
from time import monotonic
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from travelmind.schemas.agents import ErrorInfo

Provider = Literal["bocha", "firecrawl", "openalex", "social", "cnkgraph"]
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


class ProviderError(ErrorInfo):
    provider: Provider


class ProviderFailure(Exception):
    def __init__(self, error: ProviderError):
        super().__init__(error.message)
        self.error = error


def failure(
    provider: Provider, code: str, message: str, *, retryable: bool = False
) -> ProviderFailure:
    return ProviderFailure(
        ProviderError(provider=provider, code=code, message=message, retryable=retryable)
    )


def invalid_response(provider: Provider) -> ProviderFailure:
    return failure(provider, "INVALID_RESPONSE", "研究服务返回了无法识别的响应")


def status_failure(provider: Provider, status: int) -> ProviderFailure:
    if status == 401:
        return failure(provider, "AUTHENTICATION_FAILED", "研究服务认证失败")
    if status in (402, 403):
        return failure(provider, "ACCESS_DENIED", "研究服务拒绝访问，请检查权限及额度")
    if status == 429:
        return failure(provider, "RATE_LIMITED", "研究服务请求已达限额", retryable=True)
    if status == 408 or 500 <= status <= 599:
        return failure(provider, "UPSTREAM_UNAVAILABLE", "研究服务暂时不可用", retryable=True)
    return failure(provider, "REQUEST_FAILED", f"研究服务拒绝了请求（状态 {status}）")


def retry_delay(response: httpx.Response | None) -> float:
    value = response.headers.get("Retry-After") if response is not None else None
    if value is None:
        return 0.25
    try:
        if value.isascii() and value.isdecimal():
            return float(value)
        date = parsedate_to_datetime(value)
        return max(0.0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 0.25


class ProviderClient:
    def __init__(
        self, client: httpx.AsyncClient, provider: Provider, key: SecretStr | None, timeout: float
    ):
        self.client = client
        self.provider = provider
        self.key = key.get_secret_value().strip() if key else ""
        self.timeout = timeout

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
        status_field: str | None = None,
        headers: dict[str, str] | None = None,
        accepted_status: int | None = None,
    ) -> dict[str, Any]:
        if not self.key and self.provider not in ("openalex", "cnkgraph"):
            raise failure(self.provider, "NOT_CONFIGURED", "研究服务尚未配置 API Key")
        headers = dict(headers or {})
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        for attempt in range(1, 3):
            started = monotonic()
            response = None
            try:
                async with (
                    asyncio.timeout(self.timeout),
                    self.client.stream(
                        method,
                        endpoint,
                        headers=headers,
                        json=json,
                        params=params,
                        timeout=self.timeout,
                        follow_redirects=False,
                    ) as response,
                ):
                    if not response.is_success and response.status_code != accepted_status:
                        raise status_failure(self.provider, response.status_code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise invalid_response(self.provider)
                        body.extend(chunk)
                try:
                    payload = loads(body)
                except (ValueError, RecursionError):
                    raise invalid_response(self.provider) from None
                if not isinstance(payload, dict):
                    raise invalid_response(self.provider)
                if status_field is not None:
                    status = payload.get(status_field)
                    if type(status) is not int:
                        raise invalid_response(self.provider)
                    if status != 200:
                        raise status_failure(self.provider, status)
            except (TimeoutError, httpx.TimeoutException):
                problem = failure(self.provider, "TIMEOUT", "研究服务请求超时", retryable=True)
            except httpx.RequestError:
                problem = failure(
                    self.provider, "NETWORK_ERROR", "研究服务连接失败", retryable=True
                )
            except ProviderFailure as exc:
                problem = exc
            else:
                logger.info(
                    "research.request provider=%s attempt=%s elapsed_ms=%.0f",
                    self.provider,
                    attempt,
                    (monotonic() - started) * 1000,
                )
                return payload

            delay = retry_delay(response)
            logger.warning(
                "research.request_failed provider=%s attempt=%s code=%s elapsed_ms=%.0f",
                self.provider,
                attempt,
                problem.error.code,
                (monotonic() - started) * 1000,
            )
            if attempt == 2 or not problem.error.retryable or delay > 2:
                raise problem from None
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")
