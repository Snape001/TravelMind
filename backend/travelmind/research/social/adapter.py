import asyncio
import logging
from itertools import zip_longest
from time import monotonic

import httpx

from travelmind.research.http import ProviderError, ProviderFailure, failure, status_failure
from travelmind.research.social.schemas import Platform, SocialEvidenceRecord, SocialPage
from travelmind.schemas.domain import Evidence

logger = logging.getLogger(__name__)
PLATFORM_LIMIT = 2
RESEARCH_LIMIT = 6
MAX_PAGE_BYTES = 96 * 1024


class SocialResearchAdapter:
    """Read approved cache entries through the local process boundary."""

    def __init__(self, client: httpx.AsyncClient, url: str, token: str, timeout: float):
        self.client = client
        self.url = f"{url.rstrip('/')}/v1/evidence"
        self.token = token
        self.timeout = timeout

    async def _read(
        self, platform: Platform, keywords: list[str], timeout: float
    ) -> list[SocialEvidenceRecord]:
        started = monotonic()
        params = [("platform", platform), ("limit", str(PLATFORM_LIMIT))]
        params.extend(("keyword", word) for word in keywords)
        try:
            async with (
                asyncio.timeout(timeout),
                self.client.stream(
                    "GET",
                    self.url,
                    params=params,
                    follow_redirects=False,
                    timeout=timeout,
                    headers={"Authorization": f"Bearer {self.token}"},
                ) as response,
            ):
                if not response.is_success:
                    raise status_failure("social", response.status_code)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_PAGE_BYTES:
                        raise ValueError("oversized social evidence page")
                    body.extend(chunk)
            page = SocialPage.model_validate_json(body)
            if len(page.items) > PLATFORM_LIMIT or any(
                item.platform != platform for item in page.items
            ):
                raise ValueError("invalid social evidence page")
        except (TimeoutError, httpx.TimeoutException):
            raise failure("social", "TIMEOUT", "本地社交资料服务超时") from None
        except httpx.RequestError:
            raise failure("social", "NETWORK_ERROR", "本地社交资料服务不可用") from None
        except ValueError:
            raise failure("social", "INVALID_RESPONSE", "本地社交资料响应无效") from None
        logger.info(
            "research.social platform=%s posts=%d comments=%d duration_ms=%.0f",
            platform,
            len(page.items),
            sum(len(item.comments) for item in page.items),
            (monotonic() - started) * 1000,
        )
        return page.items

    async def search(
        self, keywords: list[str], platforms: tuple[Platform, ...], *, timeout: float | None = None
    ) -> tuple[list[Evidence], list[ProviderError]]:
        errors: list[ProviderError] = []
        deadline = min(self.timeout, timeout if timeout is not None else self.timeout)

        async def read(platform: Platform) -> list[SocialEvidenceRecord]:
            try:
                return await self._read(platform, keywords, deadline)
            except ProviderFailure as exc:
                errors.append(exc.error)
                logger.warning(
                    "research.social_failed platform=%s code=%s duration_cap_s=%.2f",
                    platform,
                    exc.error.code,
                    deadline,
                )
                return []

        batches = await asyncio.gather(*(read(platform) for platform in dict.fromkeys(platforms)))
        seen: set[str] = set()
        by_id = {
            item.evidence_id: item
            for group in zip_longest(*batches)
            for item in group
            if item is not None
        }
        evidence = [item.to_evidence(seen) for item in list(by_id.values())[:RESEARCH_LIMIT]]
        return evidence, errors
