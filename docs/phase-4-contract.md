# Phase 4 公共接口

本基线定义未来接口和 JSON 结构。现有 FastAPI 路由、OpenAPI、Worker 与浏览器行为在 Step 0 继续使用 Phase 3 实现。[技术架构与分支计划](phase-4-architecture.md)

## 目的地与路线

`DestinationTaskCreate` 仅接受 `{"destination":"开元寺"}`，裁剪后为 1–80 字符。服务端以现有 `ResearchBrief` 的默认深度创建任务。目的地确认之前，简报中的目的地保留用户输入。确认后，服务端在现有任务锁下记录标准目的地，Planner 使用标准名称和已核对的结构化上下文。旧 `POST /tasks` 继续接受原简报。

`Destination` 以 `(provider, poi_id)` 为稳定身份。高德 `id`、`name`、`pname`、`cityname`、`adname`、`adcode`、`citycode` 和 `address` 映射至 POI ID、名称、行政区划和地址。行政区字段缺失时保留 `null`，公交路由使用供应商城市码。经度先于纬度，服务端保存原始 GCJ-02 坐标，使用显式 `longitude`、`latitude`、`crs`。缺少真实 POI ID 或坐标的结果不能确认。地理编码用于辅助核对，目标仍须回到高德 POI 结果。Adapter 按高德关键字长度限制返回可操作提示，请求参数中的密钥留在后端。 [高德 POI 2.0](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch) · [高德坐标系](https://lbs.amap.com/faq/advisory/others/39838)

`TravelPreferences` 含出发和返程当地日期、停留天数、交通方式、风格、强度、整团人民币预算、人数与同行者类别。全部可省略，`null` 表示未答，`budget_cny=0` 表示用户明确填写零预算。日期采用 ISO 日历日期，双日期填写时返程日不得早于出发日。路线记录 `TravelRoute` 含高德来源、实际交通方式、起讫 POI、查询时间、整数米/秒和可空的高德折线。一次多景点方案由多段路线组成。未返回距离或时间的路段作为资料缺口处理，不产生路线记录。

## 动态问卷

`QuestionnaireDraft` 是 Director 的结构化输出，仅含问题定义。后端为已保存的 `ResearchQuestionnaire` 分配 UUID，并从已验证结果附加候选 POI。前端发给服务端的是当前 `questionnaire_id` 路径和 `QuestionnaireSubmission`。问题及选项的短 ID 各自在所属轮次或问题内唯一，列表顺序是展示顺序。

| 轮次 | 来源 | 内容 | 后续 |
| --- | --- | --- | --- |
| `destination_search` | 确定性模板 | 无可用 POI 时请用户补充城市或区县 | 用补充后的关键词重新检索 |
| `destination_confirmation` | 高德结果 + 确定性模板 | 必选一个真实 POI，单个候选也需用户确认 | 保存该候选快照，启动 Director 问卷 |
| `research` | Director 的结构化草稿 + 保留字段 | 多选研究方向，最后显示可选自由文本 | 选中路线才产生新一轮 `travel`，其余答复交 Planner |
| `travel` | 已选路线触发的确定性字段和选项 | 日期、天数、交通、风格、强度、预算和同行人员均可跳过 | 规范化为 `TravelPreferences` 后交 Planner |

- `kind` 区分 `text`、`single_choice`、`multi_choice`、`date`、`integer`。答案带相同 `kind` 和 `question_id`，使用 `text`、`option_ids` 或 `value`。缺省和跳过以省略该题表示，整轮可跳过时提交 `{"answers":[]}`。
- 同轮 `visible_if` 精确引用前一题或更早题的真实选项。只有当前可见且已回答的父题可以激活子题。校验拒绝前向引用、未知选项、重复答案和隐藏题答复。
- 研究轮发布时保留 `interests` 多选、`travel_route` 选项和末尾 `additional_notes` 自由文本。应用确定保留字段和触发规则。LLM 可生成目的地相关选项、标签及同轮追问。校验失败按现有操作预算重试，耗尽后使用确定性问题集合。高德候选、路线数据和工具权限均由应用提供。
- 高德成功返回零个合格候选时进入目的地细化。超时、限流或配置错误沿用任务错误与重试规则，前端显示真实失败原因。没有已确认的目的地时不进入 Planner。
- 路线轮按 `TravelPreferences` 的键命名。日期和整数直接映射；交通与强度采用定义的枚举 ID；风格和同行类别采用已发布选项的稳定 ID。应用对组装后的 `TravelPreferences` 进行整体验证。已保存的题目与答复、确认的 POI 及侧写跟随任务和 Checkpoint 恢复，重放不会重新生成同轮。
- `ResearchIntakeContext` 供 Planner 使用，包含已确认地点、已校验的研究轮问卷与答复、是否请求路线和可空的规划偏好。保留题目和选项标签以解释同轮追问。路线标志须与 `interests` 的答复一致。Planner 继续产出既有 `ResearchPlan`。
- 同一时刻只有一个可答轮次。发布沿用 `running → needs_clarification`，答复沿用 `needs_clarification → queued → running`。旧 `task.clarification` 事件仍发送题目文本。展示兼容用的 `TaskRead.pending_clarification` 使用同一轮次 UUID，问题 UUID 由 `UUIDv5(questionnaire_id, question_id)` 派生。Phase 4 前端从 Intake 读取结构化轮次，对 typed 轮次调用新答复端点。

## HTTP 契约

路径均相对同源 `/api/v1`。继续由会话确定身份和归属，跨用户或不存在的任务/资源返回相同 404。写操作遵循现有 CSRF 校验。现有登录、任务查询、SSE、暂停、恢复、文本澄清、报告及证据接口继续沿用。新增错误同用 `ErrorResponse`，`retryable` 遵循 [Phase 2 规则](phase-2-contract.md#错误与幂等)。

| 计划接口 | 请求 | 成功 |
| --- | --- | --- |
| `POST /tasks/from-destination` | `DestinationTaskCreate`，可选 `Idempotency-Key` | `201 TaskRead` + `Location`，幂等回读为 200 |
| `GET /tasks/{task_id}/intake` | 任务所有者 | `200 ResearchIntakeRead`。标准目的地、待答问卷、规划偏好分别可为 `null` |
| `POST /tasks/{task_id}/questionnaires/{questionnaire_id}/answer` | `QuestionnaireSubmission`，必需 `Idempotency-Key` | `202 TaskRead`，写入续跑指令并进入队列 |
| `GET /tasks/{task_id}/routes` | 任务所有者 | `200 TravelRoutesRead`，无路线时 `routes: []` |
| `GET /tasks/{task_id}/research-events` | 任务所有者，支持 `after` 或 `Last-Event-ID` | `200 text/event-stream`，见下节 |
| `GET /tasks/{task_id}/research-sources/{source_id}` | 任务所有者 | `200 ResearchSourceRead`，已授权取得并可显示的正文提供纯文本，否则为 `null` |
| `GET /experts` | 已认证用户 | `200 ExpertProfilesRead`，仅提供可公开展示的档案字段 |

新建的幂等作用域使用独立操作 `create_from_destination`，与旧创建隔离。答复使用 `answer_questionnaire` 和任务 ID。答复摘要包含路径的问卷 ID，题目与多选答案按 ID 排序，日期使用 ISO 字符串，文本裁剪首尾空白。命中同请求优先回放已保存响应。实时状态冲突返回 `TASK_STATE_CONFLICT`。过期轮次或向旧文本答复路由提交 typed 轮次返回 `409 CLARIFICATION_STALE`；非法、必答缺失、隐藏题及无法转换的组合返回 `422 INVALID_QUESTIONNAIRE_ANSWERS`。幂等冲突复用 `IDEMPOTENCY_CONFLICT`。

## Research Browser 与用量事件

在 `/research-events` 提供独立的扩展流。它复用 AgentEvent v1 的外层字段、UUID 与时间格式，拥有自己的任务内 `sequence`，使用 `ResearchBrowserEvent.payload.type` 的独立判别联合。原有 `/events` 和 AgentEvent v1 联合只接收现有事件，现有消费者可继续直接校验全部消息。两条流分别保存游标，禁止凭序号跨流比较。

每个 SSE 帧用 `id: <sequence>`、`event: <payload.type>` 和 `data: <ResearchBrowserEvent JSON>`。序号从 1 开始，`Last-Event-ID` 优先于 `after`，无游标从 0 后回放，默认按序连接预期的实时追加。事件按 `(task_id, sequence)` 去重，重复投影保持相同标识和内容。沿用 Phase 2 的 SSE 心跳、会话复查和撤销断流；终态在所有已提交扩展记录发完后关闭。历史任务允许为空。两条流的交叉引用使用 UUID。

| 事件 | 生产点 | 负载及前端用途 |
| --- | --- | --- |
| `research.source.discovered` | 检索/社交接入确认候选 | `source_id`、来源类型、标题、发布方、可选原始 URL，及发现的 `agent_id`、`run_id` |
| `research.source.fetch` | 抓取过程 | `source_id`、`attempt`、`started/succeeded/failed/skipped`、可选安全错误码。失败后可开启新的 attempt |
| `research.source.scored` | 有效评分确认 | `source_id`、真实 `evidence_id`、`assignment_id`、既有质量等级、总分、评分策略版本。多个分工的评分可分别显示 |
| `research.source.cited` | 已审核报告发布 | `source_id`、`evidence_id`、`report_id`、`report_version`。前端用当前发布版本展示「已引用」 |
| `agent.usage` | 统一模型封装收到供应商响应 | `AgentUsage`、`agent_id`、`run_id`。含真实调用 ID、可选旧流消息事件 ID、Prompt 预览、计数与模型 |

同一任务内按标准来源标识去重并复用 `source_id`。首次来源可从标题或书目定位开始；变更规范标识时保留已发布 UUID。评分和引用必须关联同任务已入库的 Evidence，未审核/被拒稿件不产生引用事件。抓取成功后先保存可读正文，再宣布成功或声明正文不可展示。正文、搜索工具原始参数、账号数据不进 SSE。前端只渲染纯文本和通过公开 HTTP(S) 校验的来源链接。

中栏优先显示 `agent.message` 与来源事件能确定的研究动作。可展开详情使用既有工具标识、耗时、状态及已发布的来源/评分字段。技术详情同样经过脱敏。

`AgentUsage` 一条对应一次实际供应商调用，重试是独立调用；回放同一次调用不双计。计数直接取模型供应商的 Usage 元数据，包括供应商计入输入的缓存 Token。缺字段用 `null`，供应商实报零保留为 0；聚合缺失数据显示「部分统计」。`provider` 和 `model` 记录响应提供的实际模型，模型字段缺失时可用已发送的模型标识。Hover 预览使用与请求指令模板对应的公开、脱敏简述，最多 280 字；缺少安全预览时为 `null`。原始系统 Prompt、用户答案、证据原文、回调参数、Cookie 和密钥均不公开。消息上的 Hover 优先使用显式 `message_event_id`，未关联消息时仅归到所属专家运行。

## ExpertProfile、Skills 与社交证据

公共 `ExpertProfile` 保留三层 `leadership/council/editorial`，复用稳定 `agent_id` 与本地肖像。包含姓名、头衔、描述、专业领域、可展示工具和 `(skill_id, version)` 引用。Runtime 调度仍以实际 Agent ID 和可信注册表为准。档案中的工具展示和 Skill 引用不会授予执行权限。

`ExpertSkill` 由可信、版本化的配置注册。包含可用 Agent、阶段、适用条件说明、输入输出 Schema 键、工具白名单、步骤、质量要求和现有 `RunBudget` 加工具次数。运行端检查 Agent 与阶段、实际触发条件和剩余预算，并按注册的 Pydantic 类型校验 `ExpertSkillInvocation.input` 和 `ExpertSkillResult.output`。结果按调用 ID 返回成功输出或既有 `ErrorInfo`。授权是运行端策略、技能白名单与当前任务能力的交集。输入、检查点和报表只使用定稿版本，回放禁止根据新的配置解释同一次调用。

`SocialEvidence` 包含平台、适配器、原帖 ID、可空样本数和既有 `Evidence(source_type="social")`。接入时经授权、最小化、去重和聚类后才构造证据摘录。模块复用 Evidence Center 去重、编号与评分。社交数据用于对体验和分歧提供有边界的佐证，计数和代表性限制应保留在结论中。

## 可执行结构与 Fixture

- [目的地、问卷与出行偏好](../backend/travelmind/schemas/phase4_intake.py)
- [专家档案与技能调用](../backend/travelmind/schemas/phase4_experts.py)
- [扩展事件与用量](../backend/travelmind/schemas/phase4_events.py)
- [来源正文、路线与社交证据](../backend/travelmind/schemas/phase4_sources.py)
- [公共 JSON Fixture](../fixtures/phase4.json) 包含同名 POI、动态问题、多轮示例、逐条来源事件、模型用量、路线和社交样本。数据为合成测试样例。

前端可从仓库根目录单独导出待接入的组件类型。接口路径以本文为准，集成时由真实后端 OpenAPI 生成正式类型。

```powershell
Push-Location backend
try {
    $phase4Components = & .\.venv\Scripts\python.exe -B -m travelmind.schemas.phase4_openapi
    if ($LASTEXITCODE -ne 0) { throw 'Phase 4 component export failed' }
} finally {
    Pop-Location
}
New-Item -ItemType Directory -Force .artifacts | Out-Null
$phase4Components | & .\frontend\node_modules\.bin\openapi-typescript.cmd -o .artifacts/phase4.generated.ts
```
