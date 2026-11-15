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
| `SOCIAL_RESEARCH_PLATFORMS` | JSON 数组，默认 `["xiaohongshu","bilibili"]`，可加 `weibo` 和 `douyin` |
| `SOCIAL_RESEARCH_TIMEOUT_SECONDS` | 默认 3 秒，上限 10 秒 |

苏听雨以目的地和最多两个调研主题构造查询，同时查找目的地关键词。匹配忽略全半角、大小写及重复空白，主题精确匹配优先。四个平台独立返回。服务超时、平台缓存为空或结果异常时，专家继续使用现有网页和学术检索。

每批最多写入 15 篇原帖和每篇 12 条去重评论或回复。先按平台、关键词和原帖 ID 去重，再读取分页缓存。同一关键词的更新使用最新采集时间。入库前丢弃账号字段、带查询串链接和媒体信息，清理常见联系方式，并保留评论 ID 和父评论 ID 在本地缓存中。提交给模型的证据最多 6 篇原帖、每平台 2 篇和每篇 6 条不同评论。导入者须检查自由文本并确认允许向 TravelMind 使用的模型供应商发送摘录。

Sidecar 的 `SocialEvidenceRecord` 记录原帖和评论样本。交给 Research Engine 时转换为冻结的 `SocialEvidence`，最后使用 `Evidence` 的公开链接和 ID 入库。`AgentEvent v1`、现有任务 API、Checkpoint 格式及状态机沿用原协议。苏听雨把旅游体验情感、主题、投诉、营销迹象和体验综合输出为现有结论结构。引用社交证据的报告章节自动附上平台、参与引用的原帖和评论条数及 UTC 采集时间。

## 验收记录（2026-11-15）

| 平台 | Windows 独立进程跑通的测试输入 | 真实搜索、详情和评论 |
| --- | --- | --- |
| 小红书 | 合成笔记 1 篇、评论 1 条；另测回复关联和链接去令牌 | 未执行，待核对允许的访问方式并取得样本。真实数据 0 篇、0 条 |
| B站 | 合成视频 1 个、评论 1 条；另测平台评论数与样本数区分 | 未执行。用户协议对自动化获取明确要求事先书面许可。真实数据 0 个、0 条。 [用户协议](https://www.bilibili.com/blackboard/protocal/licence.html) |
| 微博 | 合成帖子 1 篇、评论 1 条；另测缺文件错误和渠道失败隔离 | 未执行。协议禁止未经书面许可的自动化行为。真实数据 0 篇、0 条。 [服务使用协议](https://www.weibo.com/signup/v5/protocol/) |
| 抖音 | 合成视频 1 个、评论 1 条 | 未执行，未取得可用的授权样本。真实数据 0 个、0 条。 [用户服务协议](https://www.douyin.com/draft/douyin_agreement/douyin_agreement_user.html?id=6773906068725565448) |

真实平台验收仍需小红书笔记、B站视频及其可用评论和回复，并记录微博、抖音获准访问后的结果与失败分类。取得样本后，运行同一导入流程和本机 Worker，核对原帖链接、Evidence ID、五项分析、证据评分和最终报告范围。
