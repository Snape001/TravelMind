# Evidence Quality

`travelmind.research.quality.EvidenceQualityScorer.assess` 接收现有 `AgentInput` 的分工和已持久化的 `Evidence`。调用方为同一分工的各轮提供固定的带时区 `as_of` 和 `max_age_days`。返回结果与输入顺序一致，并带回原始 Evidence ID、来源、摘录和日期。

生产 Worker 在 Research Engine 的结果去重并持久化后调用评分器。首次与增量补查均按 A/B/C/D、同级加权分确定语义评分优先级，增量轮优先新材料。专家依据冻结评分卡和语义核验生成补查动作。超出批次预算的原始材料仍保留在调研快照。

```python
from travelmind.research.quality import EvidenceQualityScorer

scorer = EvidenceQualityScorer()
decisions = await scorer.assess(
    agent_input,
    evidence,
    as_of=assignment_as_of,
    max_age_days=assignment_max_age_days,
)
direct_use = [decision.evidence for decision in decisions if decision.action == "use"]
follow_up = [decision for decision in decisions if decision.target_question is not None]
```

## 筛选与动作

权威性、时效、相关性、内容完整性和独立性各为 0–2 分。综合分为各维度分数与权重乘积之和除以 2，满分 100。每项都有依据，判定还记录触发对应动作的理由。

| `source_type` | 权威 | 时效 | 相关 | 完整 | 独立 |
| --- | ---: | ---: | ---: | ---: | ---: |
| official | 34 | 26 | 20 | 14 | 6 |
| academic | 30 | 8 | 26 | 24 | 12 |
| book | 26 | 8 | 26 | 28 | 12 |
| web | 18 | 20 | 24 | 22 | 16 |
| social | 10 | 20 | 24 | 20 | 26 |
| map | 16 | 32 | 24 | 20 | 8 |

由上到下应用首个命中的规则。

| 等级 | 判定 | 动作 |
| --- | --- | --- |
| D | 来源不可追溯、发布时间超窗或晚于参考时间、相关性为零、正文缺失，或已有模型判断指出摘录无支撑 | `exclude`，从可选材料中排除，保留原始记录 |
| C | 时效窗口要求发布日期而材料未提供，或相关性、内容完整性不足，或综合分低于 70 | `supplement_search`，以分工目标补查原始、更新或更完整的材料 |
| B | 存在同源记录，或已有模型判断要求有限推断 | `cross_verify`，针对原问题查找独立出处 |
| A | 综合分至少 85，且命中权威规则或有核实的独立来源 | `use`，进入可直接使用的候选材料 |
| B | 其余达到基本筛选要求的材料 | `cross_verify`，针对原问题查找独立出处 |

相关性优先根据 `excerpt` 的目标词项命中情况判定。调用方可通过 `retrieval_scores` 按 Evidence ID 传入当前查询已归一化到 `[0, 1]` 的检索分数。无词项命中时，至少 0.75 的检索分数可提示部分相关。现有检索结果只传回 Evidence 和错误，缺少分数时按正文判断。

独立性使用现有 `source_key`、标准 DOI/ISBN、发布主机、规范化重复摘录和已知原始发布标识判断同源。仅在调用方通过 `verified_origins` 提供已核实的原始发布标识，且有至少两个符合基本条件的不同原始发布源时，才给独立性满分。域名不同或仅有一份资料时，按独立性待核实处理。检索和交叉核实的实际调度由调用方依据动作和 `target_question` 执行。

## 冻结评分卡

调用方可以注入现有 Runtime 的 `StructuredModel` 和实际 `model_id`。默认只对通过溯源、时效和正文检查、相关性仍不明确的材料请求语义判断。下游需要完整的冻结评分卡时使用 `require_contract_cards=True`，从 `decision.quality` 读取通过验证的 `EvidenceQuality`。评分卡仍按 [Phase 3 v1](phase-3-contract.md#evidencequality) 实施五维等权及 `high/medium/low`。A/B/C/D 分数、内容完整性和独立性用于证据筛选。

语义调用每批最多 4 条，每条提交最多 4,000 个摘录字符，复用调用方的 `RunBudget` 超时和重试。应用先检查该批的 Evidence ID，再逐条核对字面摘录。失败的条目保留规则结果，同批通过核验的评分卡照常保留。成功结果存入最多 256 条的实例缓存，按任务、分工、简报、时间窗口、模型、契约版本和证据快照区分。重放或补查时复用同一评分器可复用已经核验的语义判断。
