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

## Worker 与持久化

默认 Worker 的地点确认和旅游规划共用 `AmapClient`，规划端传入 Worker 级 `AmapResultCache`。完成结构化问卷的新任务启用 `durable-v4`，以已校验的路线选择为准增补或移除 `travel_planning` 分工。该分工调用 `TravelPlanner.invoke`，普通专家继续使用反思流程。旧简报任务和已有 `durable-v1`、`durable-v2`、`durable-v3` Checkpoint 按原图运行。

地图快照和所引用的地图证据由同一带租约校验的投影事务写入 PostgreSQL。审核通过时，报告发布事务将带 `report_id` 的快照保存到报告版本。重放已提交的 Checkpoint 可重建未投影的地图快照，旧租约写入仍会被拒绝。

## 地图查询

两条接口均以当前会话和任务所有权为准，跨用户资源返回 404。

| 接口 | 返回 |
| --- | --- |
| `GET /api/v1/tasks/{task_id}/routes` | 冻结的 `TravelRoutesRead`，按日期和路段顺序返回已选路线 |
| `GET /api/v1/tasks/{task_id}/reports/{report_id}/map-data` | `MapData`，含关联 ID、完整性状态、真实 POI、每日停留与交通选择、备选路线、独立几何分段及缺口 |

按 `days[].legs` 将路线 ID 关联到 `routes[].route.route_id`。点位和路线均为经度在前的 GCJ‑02 坐标。优先逐段渲染 `segments[].path`，公交分段另含实际线路、上下车站和供应商站点位置。分段间明显偏离时单一 `route.polyline` 留空，仍可绘制各自存在的分段。距离、预计耗时、来源和查询时间以高德返回为准；已选路线提供可查询的地图 Evidence ID。

`status=partial` 表示已尝试规划并存在未核对的路线、交通方式或几何。报告正文显示地图数据不完整，普通调研继续。旧报告及未请求路线的报告地图返回 `status=unavailable`。迁移 `0005` 新增任务快照和可空的报告快照字段，历史报告正文及 AgentEvent v1 保持原格式。 [Phase 4 公共接口](phase-4-contract.md)
