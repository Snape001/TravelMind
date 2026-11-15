# 旅游规划与高德地图

陆知行使用既有 `travel_planning` Agent ID，登记在专业专家层。已确认 POI 及研究答复组成 `ResearchIntakeContext`。用户在问卷选择 `travel_route` 时，Planner 应分配一次旅游规划任务。可选偏好整轮跳过时仍能执行，按一日、适中强度、步行和公交提供条件性方案。相关需求可由 Director 引导用户确认该选项。

## 本地配置

| 文件 | 配置 | 用途 |
| --- | --- | --- |
| `backend/.env` | `AMAP_WEB_API_KEY` | A 线后端接收 Web 服务类型 Key，供目的地确认和旅游规划共用 |
| `backend/.env` | `NEXT_PUBLIC_AMAP_JS_API_KEY` | 地图加载所用 Web 端 JS API 类型 Key，由同源配置接口交给前端 |
| `backend/.env` | `AMAP_SECURITY_JS_CODE` | 同源代理使用的安全密钥，仅服务端可读 |

在高德控制台按用途创建 Key 并开通相应权限。Web 服务配置缺失时返回 `AMAP_NOT_CONFIGURED`，提示应填写的环境变量。地图前端在加载脚本前将 `window._AMapSecurityConfig.serviceHost` 设为 `${location.origin}/_AMapService`。服务端代理限定所需的高德路径和固定上游地址，由代理注入 `jscode`，只把公开 JS Key 交给浏览器。 [高德 JS API 安全密钥](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)

## 规划逻辑

- 按已确认地点的城市或区县检索景点，最多分析七个真实 POI，按区域归组。地理编码和逆地理编码返回带 GCJ‑02 的地址记录。目的地确认以包含真实 ID 和坐标的 POI 快照为准。
- 每次按偏好和坐标筛出最多两个候选，再比较已选交通方式的高德耗时和距离。连续路段保留私家车或自行车的可达条件。公交使用两端高德城市码，与已知本地日期及出发时刻一起查询。其他交通方式使用查询时的高德估计。高德未返回完整距离或耗时的路段不会生成 `TravelRoute`。公交折线支持真实响应中的嵌套字段。相邻分段接点允许最大 10 米的坐标偏差，并保留供应商原始点位。几何缺失或超过该距离时，折线字段为 `null`。 [高德路径规划 2.0](https://lbs.amap.com/api/webservice/guide/api/newroute)
- 景区停留时间按景点种类建议，和高德通勤秒数分别输出。计划以当地 09:00 开始计算每日预算，轻松、适中、紧凑分别按 240、360、480 分钟预留游览及转场。高德提供可解析的当日时段时，匹配出行日期并将等待计入预算。门票政策、预约名额、临时变更、返程、住宿接驳和未核实的费用列为待确认条件。驱车路径有高德已知限行标记时，排除该方案。
- 可选复用 Research Engine 的博查检索核对公告。保留同一景点、地区匹配的官方证据 ID 和原文，避免同名地点串用。高德 POI 中的「人均消费」不用于认定门票价格。缺失日期、费用或预约名额时保留对应缺口。 [高德 POI 2.0](https://lbs.amap.com/api/webservice/guide/api/newpoisearch) · [地理及逆地理编码](https://lbs.amap.com/api/webservice/guide/api/georegeo)
- 默认仅在当前 Worker 进程中缓存归一化查询结果，POI 和地理编码 15 分钟、路线 5 分钟。缓存键包含实际供应商端点及查询参数；相同参数的并发请求合用同一次上游响应。错误不缓存。部署可按供应商授权通过 `capacity=0` 禁用结果缓存。 [高德开放平台服务协议](https://lbs.amap.com/pages/terms/)

## Phase 4 集成

用 A 线的 `AmapClient(http, settings.amap_web_api_key)` 创建一个 Worker 级 `AmapResultCache`，再传给 `AmapPlaces` 和 `AmapRoutes`。统一 Planner 在 Phase 4 图中使用 `validate_travel_assignment(intake, plan)` 检查分工。专家节点传入已保存的 `ResearchIntakeContext` 和任务内可信证据，通过 `TravelPlanner.invoke` 校验版本、Agent ID、输入和执行时间。 `TRAVEL_PROFILE` 和 `TRAVEL_SKILL` 供档案与技能注册表使用。

C 线的 `itinerary_assessment` 可以用已存档路段的 Evidence 形成带引用的研究发现。资料由 B 线的 `travel_itinerary` 取得并形成结构化行程。

成功的 `TravelPlan` 包含地图点位、交通比较、缺口和 Evidence。用 `routes_for(task_id)` 得到冻结的 `TravelRoutesRead`，按任务和租约持久化已选路段，再由受所有权保护的 `GET /tasks/{task_id}/routes` 提供前端读取。公共 Schema 及已发布的 `durable-v1`、`durable-v2` 图保持兼容。地图 UI、JS 代理、公开档案及新图的持久化和路由挂载由各归属分支集成。 [Phase 4 公共接口](phase-4-contract.md) · [分支边界](phase-4-architecture.md#五条分支的修改边界)
