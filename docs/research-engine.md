# Research Engine 接入说明

## 高德景点资料

默认 Worker 配置 `AMAP_WEB_API_KEY` 后，林景川（`tourism_resources`）会在资料检索环节并行查询高德和网页。问卷已确认景点时直接按 POI ID 查询。确认的是行政区时，在该区域内搜索并读取最多两个景点。旧简报先尝试匹配唯一同名地点，遇到歧义时继续使用网页资料。

- 复用陆知行的 `AmapPlaces` 与 Worker 级缓存。高德详情查询请求 `business,navi`，以返回内容记录地址、电话、当日与常规营业时间、入口和出口坐标。缺失字段留空。当日营业时间标明对应的北京时间日期。
- 写入现有 Evidence Center 的 `map` 证据，包含 POI ID、采集时间和可打开的高德地图链接。林景川通过现有游览资源技能读取，资料浏览器可追踪该来源。票务、预约、临时闭馆和无障碍条件由景区官方公告核验。
- 每次执行租约复用景点查询结果，整组调用最多 8 秒并受单次专家检索预算约束。没有唯一匹配、服务限流或超时时，原有网页和学术检索继续。 [高德地点搜索 2.0](https://lbs.amap.com/api/webservice/guide/api/newpoisearch) · [高德地点标注](https://lbs.amap.com/api/uri-api/guide/mobile-web/point)

## 通用检索

从 `backend` 目录使用 `travelmind.research.engine.ResearchEngine`。调用方管理并复用 `httpx.AsyncClient`。独立 Worker 通过 `ResearchRetriever` 调用检索，并在数据库写入事务内校验当前执行租约。快速调研使用搜索摘要。深度和学术调研为每位专家尝试抓取一个网页的正文，并在同一执行租约内复用相同来源的抓取结果。学术调研并行检索博查与 OpenAlex，其他深度在博查没有结果时尝试 OpenAlex。网页抓取失败时保留可用搜索结果。

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
- `scrape(url)` 将公开 HTTP(S) 网页交给 Firecrawl 抓取 Markdown 正文。解析时排除带凭据、已知非公开主机和非公网 IP 的 URL。Firecrawl 提供最终 URL 时，保存前执行相同的校验。正文取得后以最终来源替换原搜索摘要，避免同一网页跳转前后重复入库。来源名称和发布时间可分别写入 `source_locator` 和 `published_at`。每条证据记录 UTC 采集时间，摘要或正文最多保存 20,000 个字符。外部正文作为调研资料处理。
- 两个方法均返回 `ToolResult`，其中 `evidence` 使用现有 `Evidence` Schema，`errors` 包含提供方、错误代码和是否可重试。并发检索时，成功来源的结果仍会返回。网络故障、超时、限流和上游 5xx 最多尝试两次。若 `Retry-After` 超过 2 秒，则将错误交给调用方处理。单次 HTTP 请求超时由 `RESEARCH_TIMEOUT_SECONDS` 设置，默认 35 秒，响应解码后最多读取 8 MiB。网页抓取的 DNS 解析和重定向由 Firecrawl 执行。
- 去重使用 DOI、网页 URL 或无 URL 时的来源定位符。网页 URL 去重时忽略片段和常见跟踪参数，保留原始引用链接及有意义的查询参数。搜索返回顺序遵循 `sources` 的优先级及各提供方的排序。
- `save_evidence(session, task_id, items)` 按任务保留每个来源的首条证据。调用方管理事务。并发写入同一任务时复用已存储的 Evidence ID，后续引用应取自函数返回值。持久化证据可通过现有 `GET /api/v1/tasks/{task_id}/evidence/{evidence_id}` 读取。

Phase 3 Worker 使用保存后的证据和分工上下文执行 Evidence Quality。确定性筛选决定语义评分的处理顺序，冻结评分卡供专家反思和独立审核。保存的原始证据仍通过既有访问接口追溯。 [Evidence Quality](evidence-quality.md)

供应商接口参见 [博查 Web Search](https://github.com/Bocha-Labs/bocha-skills/blob/main/bocha-web-search/SKILL.md)、[Firecrawl Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape) 和 [OpenAlex Works](https://help.openalex.org/data/works/)。

## 游记与地方志

在 `backend/.env` 设置 `CNKGRAPH_ENABLED=true` 后重启 Worker。古籍文献知识图谱网（CNKGraph）向非商业学习与研究开放古籍接口，实测可匿名调用。官方开放资源页未公布固定价格、额度或服务承诺。默认关闭，阅读范围取决于收录的古籍及数字底本。 [开放资源与使用范围](https://api.cnkgraph.com/Home/OpenResources) · [官方接口示例](https://open.cnkgraph.com/Api/postman.zip)

- 沈观澜在深度和学术调研中使用目的地与简报中的具体主题检索。每次 Worker 执行租约最多查一次首屏，最多读取两卷，同一租约的补查复用结果。单次请求超时最多 3 秒，整个古籍查询最多 8 秒并受专家预算约束。
- 通过 `POST /Api/Book/Find` 检索正文，再用 `GET /api/book/volume/{volumeId}` 读取选中的卷。优先引用与命中词对应的原页。卷页匹配失败时保留 API 返回的上下文并标为「检索摘录」。空结果和接口故障时仍可用原有渠道检索。
- 入库使用现有 `book` 类型，保存书名、卷页、可核对的底本标识、正文摘录、卷链接及采集时间。卷链接指向提供方 JSON。每卷保留一条摘录，最多 1,200 字符。历史记述适用于当时的背景，古今地名对应和景区现状须另行核验。
- 从 `backend` 目录可用 `await research.search("泉州府 城池", sources=("cnkgraph",), limit=2)` 验证适配器。历史文化专家也会通过现有 Evidence Center 持久化结果，报告用 Evidence ID 引用。
