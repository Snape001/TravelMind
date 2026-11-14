# A 线 Agent Runtime

`travelmind.runtime.workflow.build_research_graph` 编排 Research Director、Planner、专家研究、交叉质询、Writer 和 Reviewer。模型请求复用 `AgentInput`，节点结果使用现有的 `AgentOutput` 类型。`ResearchState` 在 Runtime 内部流转。

## 调研流程

1. Research Director 整理 `ResearchBrief`。影响研究方向的信息缺失时，图返回 `needs_clarification` 和 `questions`。调用方将用户回答合入简报后，可使用原 `task_id` 与 `trace_id` 发起下一次图执行。
2. Planner 从历史文化、自然地理、旅游资源、社交舆情和旅游规划五个角色中选出 3–5 个专家，生成任务目标和依赖。图校验专家名册与依赖关系，并生成 `ResearchPlan`。
3. Runtime 每轮通过 LangGraph `Send` 并行派发依赖已结束的任务。成功的依赖提供结论与引用。必要依赖失败时，其下游专家会收到 `DEPENDENCY_FAILED`，其他任务继续。没有可用研究结果时任务失败。
4. 两个或以上专家取得研究结果时，每位专家质询另一位专家的一条结论，并由对方依据对应证据答复。质询失败时，流程保留已有结论。Writer 汇总各专家成果及答复。
5. Reviewer 检查报告正文、证据和署名。未知引用及无对应贡献的署名会被退回。Writer 带审核意见修订，默认最多返工一次。审核通过后，图发布 `report.ready`，耗尽返工次数则以 `REVIEW_REJECTED` 结束。

## 后端接入

```python
from uuid import uuid4

from travelmind.config import get_settings
from travelmind.runtime.model import create_structured_model
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.domain import ResearchBrief

graph = build_research_graph(create_structured_model(get_settings()))
inputs = {
    "task_id": uuid4(),
    "trace_id": uuid4(),
    "brief": ResearchBrief(destination="泉州", topics=["历史文化", "遗产参观"]),
}
```

- `await graph.ainvoke(inputs)` 返回最终状态。调用方也可以用 `graph.astream(inputs, stream_mode=["custom", "values"])` 在同一次执行中消费事件载荷和状态。
- `retrieve` 可传入 `async (AgentInput) -> list[Evidence]`。资料检索层提供证据 ID、来源和摘录。同一证据 ID 需对应同一条记录。省略 `retrieve` 时，专家将缺少资料的事项标为待核实。
- `budget` 使用 `RunBudget`，分别约束每次检索或模型操作的超时和尝试次数，并限制每次模型调用的输出 Token。`max_revision_rounds` 可配置为 0–2。
- `custom` 流输出已有 `EventPayload`。API/SSE 接入层负责补齐 `AgentEvent` 的任务 ID、追踪 ID、事件 ID、顺序和时间，并保存报告、证据及任务状态。

当前运行入口是后端 Python 调用。外部检索 Adapter、任务执行持久化和 SSE 事件落库由对应模块接入。LangGraph 图在内存中运行，用户补充信息后由调用方使用更新的简报重新发起。
