import asyncio
import logging
from time import monotonic

import httpx
from pydantic import SecretStr

from travelmind.runtime.execution import RunError

logger = logging.getLogger(__name__)
_RATE_LIMITS = {
    "10003",
    "10004",
    "10010",
    "10014",
    "10019",
    "10020",
    "10021",
    "10029",
    "10044",
    "10045",
}
_TRANSIENT = {"10015", "10016", "10017"}


class AmapClient:
    """Server-side Web API client shared by POI and route adapters."""

    def __init__(self, http: httpx.AsyncClient, key: SecretStr | None, timeout: float = 5):
        self.http = http
        self.key = key
        self.timeout = timeout

    async def get(self, path: str, **params: str | int) -> dict:
        key = self.key.get_secret_value().strip() if self.key else ""
        if not key:
            raise RunError("AMAP_NOT_CONFIGURED", "高德地点服务尚未配置 API Key")
        started = monotonic()
        try:
            async with asyncio.timeout(self.timeout):
                response = await self.http.get(
                    httpx.URL("https://restapi.amap.com").copy_with(path=path),
                    params={**params, "key": key},
                    timeout=self.timeout,
                    follow_redirects=False,
                )
            if response.status_code == 429:
                raise RunError("AMAP_RATE_LIMIT", "高德服务请求已达限额", retryable=True)
            if response.status_code >= 500:
                raise RunError("AMAP_UNAVAILABLE", "高德服务暂时不可用", retryable=True)
            if not response.is_success:
                raise RunError("AMAP_REJECTED", "高德拒绝了请求，请检查服务权限及配置")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            if payload.get("status") in ("1", 1):
                return payload
            if payload.get("status") not in ("0", 0):
                raise ValueError
            code = str(payload.get("infocode"))
            if code in _RATE_LIMITS:
                raise RunError("AMAP_RATE_LIMIT", "高德服务请求已达限额", retryable=True)
            if code in _TRANSIENT:
                raise RunError("AMAP_UNAVAILABLE", "高德服务暂时不可用", retryable=True)
            if code in {"20000", "20012"}:
                raise RunError("AMAP_INVALID_KEYWORD", "高德未接受本次查询，请检查地点名称")
            raise RunError("AMAP_REJECTED", "高德拒绝了请求，请检查服务权限及配置")
        except (TimeoutError, httpx.TimeoutException):
            problem = RunError("AMAP_TIMEOUT", "高德服务请求超时", retryable=True)
        except httpx.RequestError:
            problem = RunError("AMAP_UNAVAILABLE", "无法连接高德服务", retryable=True)
        except (ValueError, RecursionError):
            problem = RunError("AMAP_INVALID_RESPONSE", "高德返回了无法读取的结果", retryable=True)
        except RunError as exc:
            problem = exc
        finally:
            # httpx exception messages and URLs can carry the Key.
            logger.info(
                "amap.request path=%s duration_ms=%.0f", path, (monotonic() - started) * 1000
            )
        logger.warning("amap.request_failed path=%s code=%s", path, problem.info.code)
        raise problem from None
