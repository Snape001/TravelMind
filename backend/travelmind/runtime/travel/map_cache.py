import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, TypeVar, cast

import httpx

from travelmind.runtime.execution import RunError

CacheKey = tuple[str, tuple[tuple[str, str], ...]]
Result = TypeVar("Result")


class AmapTransport(Protocol):
    async def get(self, path: str, **params: str | int) -> dict: ...


def _hide_amap_query(record: logging.LogRecord) -> bool:
    if isinstance(record.args, tuple):
        record.args = tuple(
            arg.copy_with(query=None, fragment=None)
            if isinstance(arg, httpx.URL) and arg.host == "restapi.amap.com"
            else arg
            for arg in record.args
        )
    return True


def invalid_response() -> RunError:
    return RunError("AMAP_INVALID_RESPONSE", "高德地图返回了无法识别的数据", retryable=True)


@dataclass
class _Pending:
    lock: asyncio.Lock
    users: int = 0


class AmapResultCache:
    """Reuse A's server transport and cache only typed public API results in memory."""

    def __init__(
        self,
        transport: AmapTransport,
        *,
        capacity: int = 128,
        monotonic_clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._transport = transport
        self._capacity = capacity
        self._clock = monotonic_clock
        self._now = now
        self._cache: OrderedDict[CacheKey, tuple[float, object]] = OrderedDict()
        self._pending: dict[CacheKey, _Pending] = {}
        self._connections = asyncio.Semaphore(4)
        # HTTPX INFO messages otherwise include the Amap key and the query string.
        logging.getLogger("httpx").addFilter(_hide_amap_query)

    async def request(
        self,
        path: str,
        params: dict[str, str],
        *,
        ttl: float,
        parse: Callable[[dict, datetime], Result],
    ) -> Result:
        key = (path, tuple(sorted(params.items())))
        pending = self._pending.setdefault(key, _Pending(asyncio.Lock()))
        pending.users += 1
        try:
            async with pending.lock:
                cached = self._cache.get(key)
                if cached is not None and self._clock() < cached[0]:
                    self._cache.move_to_end(key)
                    return deepcopy(cast(Result, cached[1]))
                self._cache.pop(key, None)
                async with self._connections:
                    try:
                        payload = await self._transport.get(path, **params)
                    except RunError as exc:
                        if exc.info.code == "AMAP_NOT_CONFIGURED":
                            raise RunError(
                                exc.info.code,
                                "高德地图未配置，请在后端环境变量中填写 AMAP_WEB_API_KEY",
                            ) from None
                        raise
                result = parse(payload, self._now())
                if ttl > 0 and self._capacity > 0:
                    self._cache[key] = (self._clock() + ttl, deepcopy(result))
                    if len(self._cache) > self._capacity:
                        self._cache.popitem(last=False)
                return result
        finally:
            pending.users -= 1
            if not pending.users:
                del self._pending[key]
