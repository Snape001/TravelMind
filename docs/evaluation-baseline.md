# TravelMind Research Evaluation Baseline

- 候选代码 `aedb2b7e2598e2a4e444600e2f7d62f6141d3013`
- 数据集 `dunhuang-quanzhou-v1`，评分版本 `v1`
- 数据集 SHA-256 `a1a3ecfa75dabce0e3606713f0b6ada37f78d741c5e7eecd191b6cc11f9ca2c2`
- 快照批次 SHA-256 `40789dee95701eba81d179b36c5b5dee133337a11108b78091bdc99dd4de39ab`

待审核用例的得分供回归诊断。固定响应回放和示例分别统计，真实服务快照以 `recorded` 标记。“—”表示未采集、判读不足或分母为零。复跑方式和指标口径见 [Research Evaluation](research-evaluation.md)。

| 目的地 | 待审核 | Gold |
| --- | ---: | ---: |
| 敦煌 | 18 | 0 |
| 泉州 | 18 | 0 |

未运行用例数 **33**。

## `fixture_replay` · `pending`

任务成功率 0.6667 (2/3)，共 3 个任务、3 个用例。事件轨迹异常 0/3。

| 质量指标 | 宏平均 | 已评分任务 |
| --- | ---: | ---: |
| recall@1 | 0.25 | 2 |
| precision@1 | 0.5 | 2 |
| recall@3 | 0.75 | 2 |
| precision@3 | 0.3333 | 2 |
| recall@5 | 0.75 | 2 |
| precision@5 | 0.2 | 2 |
| citation_resolution | 1 | 2 |
| citation_accuracy | 1 | 2 |
| faithfulness | 0.75 | 2 |
| completeness | 1 | 2 |

| 运行指标 | 合计 | 平均 | 已观测任务 |
| --- | ---: | ---: | ---: |
| input_tokens | — | — | 0 |
| output_tokens | — | — | 0 |
| model_calls | 30 | 10 | 3 |
| duration_ms | 26 | 8.667 | 3 |
| total_tokens | — | — | 0 |

## `demo` · `pending`

任务成功率 1 (1/1)，共 1 个任务、1 个用例。事件轨迹异常 0/1。

| 质量指标 | 宏平均 | 已评分任务 |
| --- | ---: | ---: |
| recall@1 | — | 0 |
| precision@1 | — | 0 |
| recall@3 | — | 0 |
| precision@3 | — | 0 |
| recall@5 | — | 0 |
| precision@5 | — | 0 |
| citation_resolution | 1 | 1 |
| citation_accuracy | 0.3333 | 1 |
| faithfulness | 1 | 1 |
| completeness | 0 | 1 |

| 运行指标 | 合计 | 平均 | 已观测任务 |
| --- | ---: | ---: | ---: |
| input_tokens | — | — | 0 |
| output_tokens | — | — | 0 |
| model_calls | — | — | 0 |
| duration_ms | — | — | 0 |
| total_tokens | — | — | 0 |

## 逐任务结果

### dunhuang-heritage · `fixture_replay`

- 任务 `ed89b87b-a513-4baa-b722-62a1bff3bc49`，状态 `completed`
- 来源 main LangGraph 固定模型与检索响应，按工具返回顺序保存排名
- faithfulness 0.5 (1/2)，citation_accuracy 1 (1/1)，completeness 1 (1/1)
- 判读 `codex-gpt-6-offline-2026-11-15`（model）
- `unsupported` 莫高窟洞窟全年开放至午夜。 该章唯一的引用仅说明遗产列入年份，无法支撑开放时间。
- 41 条事件、0 个 Agent 失败、0 个工具失败、0 次返工请求

### quanzhou-heritage · `fixture_replay`

- 任务 `6dc7c7ba-e3e2-40d3-a570-be4f3f3306a8`，状态 `completed`
- 来源 main LangGraph 固定模型与检索响应，按工具返回顺序保存排名
- faithfulness 1 (2/2)，citation_accuracy 1 (1/1)，completeness 1 (2/2)
- 判读 `codex-gpt-6-offline-2026-11-15`（model）
- 46 条事件、0 个 Agent 失败、0 个工具失败、1 次返工请求

### dunhuang-photo · `fixture_replay`

- 任务 `122f05d8-c8c5-4ec6-8370-eabd2d23b348`，状态 `failed`，错误 `NO_RESEARCH_RESULTS`
- 来源 main LangGraph 固定模型与检索响应，按工具返回顺序保存排名
- faithfulness — (0/—)，citation_accuracy — (0/0)，completeness — (0/1)
- `unavailable` 洞窟内禁止摄影摄像。 no judgment for this reference
- 19 条事件、3 个 Agent 失败、3 个工具失败、0 次返工请求

### quanzhou-heritage · `demo`

- 任务 `856d05aa-7d53-4fe7-aefa-d17e6a84c001`，状态 `completed`
- 来源 仓库只读示例 frontend/src/lib/demo-data.ts，把遗产问题作为事后覆盖检查
- faithfulness 1 (1/1)，citation_accuracy 0.3333 (1/3)，completeness 0 (0/2)
- 判读 `codex-gpt-6-offline-2026-11-15`（model）
- `missing` 泉州宋元海贸系列遗产于 2021 年入选世界遗产。 示例说明宋元海贸背景，正文未给出列入年份。
- `missing` 系列遗产由 22 个遗产点组成。 正文提及遗产点，未给出点数。
- 39 条事件、0 个 Agent 失败、0 个工具失败、1 次返工请求
