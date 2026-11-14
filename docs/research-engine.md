# Research Engine 接入说明

从 `backend` 目录使用 `travelmind.research.engine.ResearchEngine`。调用方管理并复用 `httpx.AsyncClient`，数据库写入使用现有的异步 session。FastAPI 的任务流程通过 `ResearchRetriever` 调用此接口。快速调研使用搜索摘要，深度和学术调研会尝试抓取一个网页全文。学术调研并行检索博查与 OpenAlex，其他深度在博查没有结果时尝试 OpenAlex。网页抓取失败时保留可用搜索结果。

```python
import httpx

from travelmind.config import get_settings
from travelmind.db.evidence import save_evidence
from travelmind.research.engine import ResearchEngine, ToolResult


async def collect_evidence(task_id, sessions):
    async with httpx.AsyncClient() as client:
        research = ResearchEngine(client, get_settings())
        search = await research.search("泉州古城 历史文化", sources=("bocha",), limit=5)
        url = search.evidence[0].source_url if search.evidence else None
        page = await research.scrape(url) if url else ToolResult()
        papers = await research.search("Quanzhou heritage tourism", sources=("openalex",), limit=5)

    async with sessions.begin() as session:
        stored = await save_evidence(
            session, task_id, [*page.evidence, *papers.evidence, *search.evidence]
        )
    return stored, [*search.errors, *page.errors, *papers.errors]
```

- `search(query, sources=("bocha", "openalex"), limit=10)` 并发检索指定来源。单来源上限为 50 条，同一来源只调用一次。博查使用网页摘要或片段，`gov.cn` 来源标记为 `official`。OpenAlex 优先保留 DOI 链接和 OpenAlex 记录链接，重建可用摘要。缺少摘要时，证据内容仅为文献标题。
- `scrape(url)` 通过 Firecrawl 获取公开网页的 Markdown 正文，并优先记录返回的最终 URL。提供方有返回来源名称和发布时间时，适配器将其写入 `source_locator` 和 `published_at`。每条证据记录 UTC 采集时间，摘要或正文最多保存 20,000 个字符。外部正文应作为调研资料处理。
- 两个方法均返回 `ToolResult`，其中 `evidence` 使用现有 `Evidence` Schema，`errors` 包含提供方、错误代码和是否可重试。并发检索时，成功来源的结果仍会返回。网络故障、超时、限流和上游 5xx 最多尝试两次。若 `Retry-After` 超过 2 秒，则将错误交给调用方处理。单次 HTTP 请求超时由 `RESEARCH_TIMEOUT_SECONDS` 设置，默认 35 秒。
- 去重使用 DOI、网页 URL 或无 URL 时的来源定位符。网页 URL 去重时忽略片段和常见跟踪参数，保留原始引用链接及有意义的查询参数。搜索返回顺序遵循 `sources` 的优先级及各提供方的排序。
- `save_evidence(session, task_id, items)` 按任务保留每个来源的首条证据。调用方管理事务。并发写入同一任务时复用已存储的 Evidence ID，后续引用应取自函数返回值。持久化证据可通过现有 `GET /api/v1/tasks/{task_id}/evidence/{evidence_id}` 读取。

供应商接口参见 [博查 Web Search](https://github.com/Bocha-Labs/bocha-skills/blob/main/bocha-web-search/SKILL.md)、[Firecrawl Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape) 和 [OpenAlex Works](https://help.openalex.org/data/works/)。
