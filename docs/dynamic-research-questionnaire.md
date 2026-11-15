# 目的地确认与动态问卷

首页只提交目的地。已登录用户直接创建待处理任务。需要登录时，同一浏览器标签页保留输入，完成现有 OIDC 回调后回填首页。历史、示例和报告沿用现有入口。接口、请求和事件格式以 [Phase 4 公共接口](phase-4-contract.md) 为准。

## 地点确认

在 Worker 的环境或 `backend/.env` 设置 `AMAP_WEB_API_KEY`，使用高德 Web 服务类型 Key，并开通 POI 2.0 和输入提示权限。Worker 先按关键词搜索，在没有精确名称匹配时查询高德输入提示。提示中的 POI ID 经详情接口核对后进入候选。浏览器在提交关键词后显示候选的名称、行政区和地址。单一候选也需用户确认，零候选时可补充城市、区县或更正地点名称。高德失败通过原有任务状态事件显示原因。

`AmapClient.get(path, **params)` 固定访问高德 Web 服务域名，供 POI 和路线适配器共用。地点适配器每次查询至多生成十个候选，保持供应商 POI ID 和经度在前的 GCJ-02 坐标。 [高德 POI 2.0](https://lbs.amap.com/api/webservice/guide/api/newpoisearch) · [高德输入提示](https://lbs.amap.com/api/webservice/guide/api-advanced/inputtips)

## 问卷与恢复

- Director 在确认后使用统一模型封装生成 `QuestionnaireDraft`。一轮最多两次调用，每次限 15 秒和 1600 输出 Token。发布研究轮最多七题，含多选方向、最多五个条件追问和末尾的个性化补充。模型失败或校验不通过时使用预置问题。
- 选中「旅游路线」后才发布出行偏好，时间、交通与节奏、预算与同行分三步，整轮可跳过。预算零元按 `0` 保留。服务端校验必答、可见条件、选项、日期和跨字段组合。
- intake 轮次先写入 PostgreSQL，并通过 AgentEvent v1 通知工作台。提交答复、状态变化、幂等结果和续跑在同一事务完成。Worker 重新领取时读取已保存的数据。待答轮次刷新后可继续填写，提交过的答案会跟随任务保存。
- 完成 intake 后，Planner 收到已确认的 Destination、研究问卷、答复、路线标志和可空的 TravelPreferences。旧简报创建继续进入现有图，typed 任务使用两种现有图版本并在进入图之前完成 intake。

## 部署与验证

从 `backend/` 运行 `alembic upgrade head` 应用迁移 `0004`，然后启动同版本 API、Worker 与前端。旧版本 Worker 无法解释 typed intake，回退前应停掉 API 和 Worker。`alembic downgrade 0003` 会结束未完成的 typed 任务并移除问卷数据，已发布报告仍可访问。

`backend/tests/test_intake.py` 使用可控的高德和结构化模型响应，校验地点消歧、问卷重读、幂等、隐藏答案、偏好转换和 Planner 交接。PostgreSQL 用例沿用现有 `TEST_DATABASE_URL`。前端测试读取共享 Phase 4 Fixture。此次浏览器验收使用生产 Next.js、实际 FastAPI 和 PostgreSQL，外部采用合成响应。另用合成地点独立执行真实 DeepSeek 结构化生成。
