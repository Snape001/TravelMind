# Phase 4 · 社交资料接入

TravelMind 的本地 Sidecar 导入经授权的 MediaCrawler JSONL，也接受自身导出的规范化 JSONL。苏听雨通过 Research Engine 按关键词读取缓存。已入库的观测记录走现有 Evidence Center、EvidenceQuality、报告引用和独立审核。

## 上游与使用范围

- MediaCrawler 项目版本 `0.1.0`，固定提交 `306ab4ca26c0e44fb87ede0e40e98748752815a6`，依赖由该提交的 `uv.lock` 固定。安装在本地独立 checkout，保留原始 LICENSE、README 和源码版权标头。TravelMind 产品构建不包含上游源码。 [固定版本](https://github.com/NanmiCoder/MediaCrawler/tree/306ab4ca26c0e44fb87ede0e40e98748752815a6) · [原始许可证](https://github.com/NanmiCoder/MediaCrawler/blob/306ab4ca26c0e44fb87ede0e40e98748752815a6/LICENSE)
- 上游使用 **Non-Commercial Learning License 1.1**。本接入限个人非商业学习与研究。商业使用需取得上游权利人书面同意。平台的自动化、数据使用及引用范围另按各平台授权和规则确认。
- 固定版本的小红书、B站、微博和抖音采集器均含反检测逻辑。此交付的 Sidecar 只处理本地文件，安装脚本也只准备运行依赖。真实搜索、详情和评论采集须先确定允许的访问方式、平台授权及数据使用范围。遇到验证码、拒绝访问或限流时停止该渠道的验证。
- 账号会话、Cookie、浏览器资料和导出保存在个人 Windows 用户目录。提交、共享测试 Fixture 和报告里禁止放入这类材料。小红书在缓存和报告中使用去掉查询串的原帖 URL，有些原帖仍可能要求通过平台正常登录访问。

## Windows PowerShell

先安装 Git、uv 和 CPython 3.11。在 TravelMind 根目录执行。默认目录为 `%LOCALAPPDATA%\TravelMind\social-research`。同一个数据目录运行一个 Sidecar 实例。

```powershell
.\dev\social-research\social.ps1 -Action Setup
```

上游依赖安装在 `MediaCrawler\.venv`，本地服务依赖安装在 `sidecar-venv`。如需准备独立的 Playwright Chromium，给 `Setup` 加 `-InstallBrowser`。该安装步骤不打开平台页面。用 `-Root` 可以指定其他私有目录，后续 `Start` 和导入文件目录使用同一个根目录。

在私有环境中设置 `SOCIAL_RESEARCH_TOKEN`，使用随机值，并让 Sidecar、终端客户端与同机 Worker 使用同一个值。以下命令在前台启动服务，`Ctrl+C` 停止服务。

```powershell
.\dev\social-research\social.ps1 -Action Start
```

另开一个同样配置令牌的 PowerShell。为每个授权导出生成一个批次 ID，暂存目录内只放本次批次文件。

```powershell
$social = '.\dev\social-research\social.ps1'
$socialRoot = Join-Path $env:LOCALAPPDATA 'TravelMind\social-research'
$batchId = [guid]::NewGuid()
$batchPath = Join-Path $socialRoot "data\inbox\$batchId"
New-Item -ItemType Directory -Path $batchPath
& $social -Action Status
```

拷贝同一平台、关键词、批次的原帖和评论文件，命名为 `contents.jsonl` 和 `comments.jsonl`。保留 TSV/CSV 等原始副本时先按固定版本的字段转换为 UTF-8 JSONL。评论文件缺失时允许导入原帖，记录 `partial`。导入规范化格式时，改用 `evidence.jsonl` 和 `-InputFormat normalized`，批次元数据须匹配文件。以下命令中的 `$collectedAt` 填实际采集时间，使用带时区的 ISO 8601。

```powershell
& $social -Action Import -BatchId $batchId -Platform bilibili -Keyword '泉州 预约' -CollectedAt $collectedAt
& $social -Action Job -BatchId $batchId
& $social -Action Read -Platform bilibili -Keyword '泉州 预约' -Limit 10 -Offset 0
& $social -Action Read -Platform bilibili -Keyword '泉州 预约' -JsonlPath (Join-Path $batchPath 'evidence.jsonl')
```

`Stop -BatchId <UUID>` 取消正在导入的任务。查询服务、任务和结果的本地接口分别为 `GET /v1/status`、`GET /v1/jobs/{batch_id}` 和 `GET /v1/evidence`。`POST /v1/jobs` 启动导入，`DELETE /v1/jobs/{batch_id}` 取消。重复提交同一个批次 ID 会返回已有任务状态。修正过的文件使用新批次 ID。任务状态含导入耗时、原帖数、保留评论数、拒绝行数及错误码。`data\social.sqlite3` 保存缓存和状态。

## Worker 配置与样本

在个人使用的本机 Worker 启动环境配置以下变量，令牌通过私有 `backend/.env` 或进程环境注入。同一 Worker 的匹配任务共用本地缓存。配置适用于宿主机运行的 Windows Worker。当前 Compose 默认保持社交功能关闭，Sidecar 的回环地址与容器内回环地址互相隔离。

| 变量 | 值 |
| --- | --- |
| `SOCIAL_RESEARCH_PROVIDER` | `mediacrawler_export`。默认 `disabled` |
| `SOCIAL_RESEARCH_URL` | `http://127.0.0.1:18764` |
| `SOCIAL_RESEARCH_TOKEN` | 与 Sidecar 一致的本地令牌 |
| `SOCIAL_RESEARCH_PLATFORMS` | JSON 数组，默认 `["xiaohongshu","bilibili"]`，可加 `weibo`、`douyin` 和 `zhihu` |
| `SOCIAL_RESEARCH_TIMEOUT_SECONDS` | 默认 3 秒，上限 10 秒 |

苏听雨以目的地和最多两个调研主题构造查询，同时查找目的地关键词。匹配忽略全半角、大小写及重复空白，主题精确匹配优先。平台独立返回，预算优先从每个平台取一篇，再取各平台的第二篇。服务超时、平台缓存为空或结果异常时，专家继续使用现有网页和学术检索。

每批最多写入 15 篇原帖和每篇 12 条去重评论或回复。先按平台、关键词和原帖 ID 去重，再读取分页缓存。同一关键词的更新使用最新采集时间。入库前丢弃账号字段、带查询串链接和媒体信息，清理常见联系方式，并保留评论 ID 和父评论 ID 在本地缓存中。提交给模型的证据最多 6 篇原帖、每平台 2 篇和每篇 6 条不同评论。导入者须检查自由文本并确认允许向 TravelMind 使用的模型供应商发送摘录。

Sidecar 的 `SocialEvidenceRecord` 记录原帖和评论样本。交给 Research Engine 时转换为冻结的 `SocialEvidence`，最后使用 `Evidence` 的公开链接和 ID 入库。`AgentEvent v1`、现有任务 API、Checkpoint 格式及状态机沿用原协议。苏听雨把旅游体验情感、主题、投诉、营销迹象和体验综合输出为现有结论结构。引用社交证据的报告章节自动附上平台、参与引用的原帖和评论条数及 UTC 采集时间。

## 知乎

### MediaCrawler 固定版复核

| 项目 | `306ab4c` 源码行为 | 本轮真实验收 |
| --- | --- | --- |
| 二维码和 Cookie 登录 | 二维码流程等待浏览器会话 Cookie。Cookie 模式将提供的 Cookie 写入浏览器上下文。标准浏览器有本地持久化分支。手机登录是占位实现 | 未启动上游登录，账号授权及会话复用待验证 |
| 关键词搜索 | 提取器保留回答、文章和视频，含问题关联的回答可被返回，问题条目不会导出。搜索一页固定 20 条，配置低于一页时会提升到一页 | 问题 0、回答 0 |
| 问题和回答详情 | 详情分发可识别带回答 ID 的链接。纯问题链接没有处理分支，也没有问题下回答列表的采集入口 | 问题 0、回答 0 |
| 回答评论和回复 | 根评论和子评论按游标分页，子评论由独立开关启用。知乎的调用和分页循环未使用通用单帖评论上限。Sidecar 的保留条数只限制导入，无法限制上游请求 | 根评论 0、回复 0 |
| 入库与分析 | 既有知乎 Adapter 可导入获准使用的 JSONL 并生成 `SocialEvidence`。Evidence Center 接收现有证据格式，苏听雨读取同一证据链 | 本轮仅运行 5 项离线单元测试，真实数据入库及分析待验收 |

上游标准浏览器模式加载反检测脚本，CDP 启动器包含反检测参数，两条入口在登录后都会主动打开站内搜索页。知乎 robots.txt 对通用爬虫限制相关路径，MediaCrawler 的源码使用声明要求遵守平台规则和 robots.txt。基于这些限制，本轮执行离线 CLI 帮助检查和源码复核，未启动上游爬虫。比对远端 HEAD `4d629b52eddef9c53b5e9a52b76ec4a64383b494` 后，整个差异只有 README，保留当前固定版。 [固定版登录实现](https://github.com/NanmiCoder/MediaCrawler/blob/306ab4ca26c0e44fb87ede0e40e98748752815a6/media_platform/zhihu/login.py) · [固定版采集实现](https://github.com/NanmiCoder/MediaCrawler/blob/306ab4ca26c0e44fb87ede0e40e98748752815a6/media_platform/zhihu/core.py) · [知乎 robots.txt](https://www.zhihu.com/robots.txt)

### 手动登录

```powershell
.\dev\social-research\social.ps1 -Action ZhihuLogin
```

脚本打开 Edge 或 Chrome 的独立资料目录，由用户在知乎页面手动登录、搜索关键词、查看有访问权限的问题、回答、评论和回复。完全关闭窗口后再次执行可复用浏览器保存的会话，具体有效期由知乎决定。浏览器将 Cookie 保存在 `%LOCALAPPDATA%\TravelMind\social-research\browser_data\zhihu`，该目录独立于 `-Root` 参数，仓库也忽略 `browser_data/`。普通浏览器会话和开放平台 API 使用各自的凭据。请勿导出或粘贴 Cookie。

对允许导出并分析的材料，可以用原版 `Import -Platform zhihu` 导入。原帖记录包含 `content_type`（`question` 或 `answer`）、`content_id`、`title` 及可用的 `content_text`、`content_url`、`question_id`。评论带原帖的 `content_type` 和 `content_id`，以及 `comment_id`、`content` 和可选 `parent_comment_id`。问题和回答的 ID 分开去重，回答的短链接和问题下的完整链接统一识别为同一条证据。

### 人工样本导入与验收

手动摘录使用规范化 `evidence.jsonl` 和 `Import -Platform zhihu -InputFormat normalized`。设置 `provider: "manual_browser"`、`content_scope: "excerpt"` 和 `collection_status: "partial"`。保留问题或回答的原链接。评论原始 ID 缺失时可用本地稳定 ID 保留父子关系。未提供发布时间时留空，采集时间记录该批素材的实际摘录或录入时间。Evidence Center 的来源说明标注「人工浏览摘录」。

2026-11-15 用一篇用户提供的真实知乎回答摘录、一条根评论和一条回复完成 Sidecar 导入及查询、Research Engine 去重和独立 PostgreSQL 数据库写入，再通过已认证的证据接口读取记录。苏听雨使用 DeepSeek 生成五项结构化分析，引用均指向此条证据。蟳埔村拍照的预约排队与泉州古城打车的描述各自作为个案，样本无法支撑高频判断、不同季节或时段对比。这次只录入回答，问题链接用于关联上下文。

### 开放平台采集

先启动本机 Sidecar。在调用终端本地设置 `SOCIAL_RESEARCH_TOKEN` 和拥有对应权限、额度的 `ZHIHU_ACCESS_SECRET`。命令返回导入任务状态，使用 `Job` 查看结果，使用 `Read -Platform zhihu` 按关键词查询或导出。

```powershell
$social = '.\dev\social-research\social.ps1'
& $social -Action ZhihuCollect -Keyword '泉州 预约'
& $social -Action ZhihuCollect -Keyword '泉州 预约' -ZhihuMode answers -SourceUrl $questionUrl
& $social -Action ZhihuCollect -Keyword '泉州 预约' -ZhihuMode owned -SourceUrl $ownAnswerUrl
& $social -Action Read -Platform zhihu -Keyword '泉州 预约'
```

| 模式 | 接口范围 | 预算 |
| --- | --- | --- |
| `search` | 关键词搜索。导入问题、回答摘要和接口返回的精选评论。精选评论没有原始评论 ID 或回复关系，按正文生成本地去重键 | 一次请求，最多 10 个上游结果 |
| `answers` | 由知乎问题链接获取回答列表和摘要。问题正文通过正常浏览或授权导出取得 | 最多两页，每页请求 5 条 |
| `owned` | 由本人已发布的回答链接获取正文和评论，以及接口附带的子评论 | 一次详情请求，最多两页评论，每页请求 5 条根评论 |

API 每次请求有 8 秒超时和响应大小限制。分页遵循服务端游标，遇到验证码、拒绝访问、限流或游标异常即停止。摘要和详情摘录分别标记；平台提供作者和溯源参数时，保留可用于引用的公开署名及开放平台标识。已导入资料进同一 SQLite 缓存，Worker 仅查询缓存。全平台默认预算仍为每批 15 篇、每篇 12 条评论或回复，单次研究最多向苏听雨提供 6 篇。 [搜索接口](https://developer.zhihu.com/docs?key=zhihu_search) · [问题回答接口](https://developer.zhihu.com/docs?key=question_answers) · [本人详情](https://developer.zhihu.com/docs?key=user_content_detail) · [本人评论](https://developer.zhihu.com/docs?key=user_content_comments)

## 验收记录（2026-11-15）

| 平台 | Windows 独立进程跑通的测试输入 | 真实搜索、详情和评论 |
| --- | --- | --- |
| 小红书 | 合成笔记 1 篇、评论 1 条；另测回复关联和链接去令牌 | 未执行，待核对允许的访问方式并取得样本。真实数据 0 篇、0 条 |
| B站 | 合成视频 1 个、评论 1 条；另测平台评论数与样本数区分 | 未执行。用户协议对自动化获取明确要求事先书面许可。真实数据 0 个、0 条。 [用户协议](https://www.bilibili.com/blackboard/protocal/licence.html) |
| 微博 | 合成帖子 1 篇、评论 1 条；另测缺文件错误和渠道失败隔离 | 未执行。协议禁止未经书面许可的自动化行为。真实数据 0 篇、0 条。 [服务使用协议](https://www.weibo.com/signup/v5/protocol/) |
| 抖音 | 合成视频 1 个、评论 1 条 | 未执行，未取得可用的授权样本。真实数据 0 个、0 条。 [用户服务协议](https://www.douyin.com/draft/douyin_agreement/douyin_agreement_user.html?id=6773906068725565448) |
| 知乎 | Windows 独立进程导入并分页导出合成问题 1 个、回答 1 条、根评论和回复各 1 条。合成证据通过 PostgreSQL 写入测试 | 真实搜索端点返回鉴权失败，当前缺少授权数据。真实问题、回答、评论和回复均为 0，实际 Cookie 会话复用待验收 |

真实平台验收仍需小红书笔记、B站视频及其可用评论和回复，并记录微博、抖音获准访问后的结果与失败分类。取得样本后，运行同一导入流程和本机 Worker，核对原帖链接、Evidence ID、五项分析、证据评分和最终报告范围。
