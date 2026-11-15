# Phase 3 公共接口 · 证据质量、专家反思与调研评测

本基线承接 PRD 的多源证据、动态追加研究、观点冲突核查和引用溯源。它定义 Evidence Center、Runtime 和离线评测之间的数据契约。可执行 Schema 和 JSON Schema 的来源是 [research_quality.py](../backend/travelmind/schemas/research_quality.py)。复用 `Evidence`、`Finding`、`ResearchResult`、`ResearchBrief` 和现有专家角色。

## EvidenceQuality

每张评分卡针对一个 `assignment_id` 下的一个 `evidence_id`。评分针对该任务目标。同一证据可在不同任务目标下获得不同分数。记录 `policy_version="v1"`、实际评分模型的 `model_id`、固定的带时区参考时间 `as_of` 和时效窗口 `max_age_days`。Runtime 在该分工开始时提供时间和时效窗口，所有轮次复用。同一批回放也使用所保存的值。`max_age_days=null` 表示该分工无需按资料年龄筛选。

| 维度 | 判断方 | 2 分 | 1 分 | 0 分 |
| --- | --- | --- | --- | --- |
| `traceability` 来源可追溯性 | 规则 | 通过既有公开目标检查的 URL，或可定位的标准文献标识 | 有非标准书目定位符 | URL 未通过检查，或无法定位原资料 |
| `authority` 来源可核实程度 | 规则 | 来源命中现有政务域识别，或归一化标识符合 DOI 格式 | 可追溯，发布方权威性未核实 | 不可追溯，或无可识别的发布方 |
| `freshness` 时效 | 规则 | 发布时间在窗口内，或无时效要求且无未来日期 | 有时效要求但发布时间缺失 | 已超过窗口或发布时间晚于参考时间 |
| `relevance` 与目标的相关性 | LLM | 直接回答目标中的问题 | 只覆盖部分问题或仅提供背景 | 不相关 |
| `directness` 摘录支撑强度 | LLM | 摘录明确陈述待核实事实或规则 | 摘录需要有限推断 | 无相关事实内容，或仅有未展开的标题 |

规则维度的 `reason` 记录命中的规则及依据字段。时效使用 `published_at`，边界按 UTC 时间差、含窗口终点计算。`retrieved_at` 仅表示采集时间。来源规则复用现有 URL 检查和 DOI 归一化。来源类型、域名和 DOI 提供出处线索，事实是否得到支撑仍需核读摘录。

LLM 维度返回 `score`、简短 `reason` 和逐字 `quote`。正分必须给摘录。应用核对摘录确实出现在对应的 `Evidence.excerpt`，模型标识由调用方填入。材料以非可信数据传入，证据 ID 和规则维度由应用提供。输出无效或调用失败时按现有预算处理，缺失的评分卡保留为空缺。

每维 0–2 分，总分 0–10。其中 8–10 为 `high`，5–7 为 `medium`，0–4 为 `low`。来源可追溯性、相关性或摘录支撑强度为 0、日期无效或超窗、有时效要求但日期缺失时，等级直接为 `low`。仅 `medium` 与 `high` 计入可用证据。等级表达材料可用性。结论是否被支撑、证据是否独立分别核查。

## ReflectionState 与 ReflectionAction

现有专家对自己的分工结果进行反思。只对影响分工目标的问题路由。对不同日期、适用范围或条件的差异，先拆分适用情境。其余问题按表中从上到下取首个命中的状态。

| 状态 | 判定 | 策略 |
| --- | --- | --- |
| Conflicting · `conflicting` | 至少两条可用、独立的证据对同一范围与时间的关键结论提出实质相反的说法 | `verify_conflict` 查原始发布、时间和适用范围，核验双方依据 |
| Low Quality · `low_quality` | 关键问题已有相关且已评分的资料，但均未达到可用等级 | `replace_sources` 优先查原始发布或更完整、更新的出处 |
| Insufficient · `insufficient` | 缺少必要内容、独立佐证或有效判断 | `expand_search` 针对具体缺口补查 |
| Sufficient · `sufficient` | 每个关键问题均有一条直接、可用的原始资料，或至少两条独立的可用证据共同支撑，且没有未解决的关键冲突 | `accept`，以 `sufficient` 结束 |

规则核查引用和等级、使用既有 `source_key` 排除同源记录、计算轮次和可用预算。相同来源或同一原始发布的转载只计一次。独立性无法确认时按一份佐证计。LLM 判定问题覆盖、结论与摘录的语义关系、转述或原始发布关系，以及对齐时间和适用范围后是否真正矛盾。模型提供判断理由及相关引用，应用校验引用存在并按规则路由。充分或冲突判断必须记录 `model_id` 和相关 Evidence ID。

`ReflectionAction` 带 `state`、`strategy`、`round`、`max_rounds`、`stop_reason`、`reason`、`evidence_ids`、`model_id` 和 `target_questions`。补查策略必填 `target_questions`，每次只处理命中的问题。接受和停止时目标问题留空，终态的缺口由 `ExpertResearchResult.unresolved_questions` 保留。

- 首轮为 `round=0`。快速、深度、学术调研分别最多补查 0、1、2 轮。追加轮次在调度时记入 Checkpoint，重放同轮不会再次计数。每次检索或模型调用仍受现有 `RunBudget` 约束。
- 判定充分就接受。判断必需但在操作重试后仍无有效输出时，以 `insufficient / stop / assessment_unavailable` 结算，保留先前已核实的发现及无法核验的问题。
- 对其余未解决状态依次检查 `budget_exhausted`（本分工无剩余追加执行额度）、`no_new_evidence`（成功执行的补查未增加去重后的来源）、`round_limit`（已达补查上限）。命中时用 `stop` 保留已确认内容与缺口。可用的相反证据和争议应保留，禁止把未证实的结论升级为 `supported`。
- 暂停与租约失效由既有 Worker 控制。仅投影租约可以补齐已保存的业务投影，禁止启动反思、补查或写入新 Checkpoint。

## 数据流与模块职责

| 模块 | 输入 | 输出与下游 |
| --- | --- | --- |
| Research Engine / Evidence Center | 现有 `AgentInput`，首次检索目标或补查问题 | 经任务内去重、持久化确认的 `Evidence` 及既有检索错误。Evidence Center 以 `Assignment`、材料和时效上下文生成 `EvidenceQuality`，检索适配器继续处理供应商 I/O |
| Agent Runtime / 现有专家 | 分工、候选 `Finding`、证据和评分、轮次及预算 | 返回 `ReflectionAction`。继续时将补查问题交回 Research Engine，结束时生成 `ExpertResearchResult` |
| Report Engine | 专家成果的 `ResearchResult`、证据快照、未解决问题 | Writer 使用已有报告 Schema 保留证据缺口及争议。Reviewer 继续进行独立审核 |
| Research Evaluation | `EvaluationCase`、候选运行的专家结果与报告，以及已有事件或反思记录 | 离线输出逐用例指标、缺失或冲突的参考发现、相关候选和参考 Evidence ID，并记录评分版本、模型与失败原因 |

`ExpertResearchResult` 含 `assignment_id`、复用的 `research: ResearchResult`、`quality`、终态 `reflection` 和 `unresolved_questions`。其中 `research.evidence` 是本次专家用到的完整快照，包含从依赖带入的证据。结论、反思和评分的所有 Evidence ID 都必须能在快照中找到。同一分工的单个 Evidence ID 只有一张评分卡。每个 `supported` 结论至少引用一条已评分且可用的证据。部分完成时可同时保留已支持的发现及未解决问题。

接入时继续用 `ResearchState.results`、`evidence` 和 `assignment_runs` 关联运行与材料。把新增结果放在可缺省的 `expert_research` 字典，以既有 `run_id` 为键。并行合并沿用 `merge_records` 的规则，允许回放相同记录，拒绝同键写入不同值。活动轮次按运行 ID 和轮次保存独立的决策及评分快照，恢复时顺序读取已保存的轮次。现有 `ResearchResult.evidence` 的增量语义和 `AgentOutput` 联合类型保持兼容。后续接线需要验证既有 `durable-v1` Checkpoint 的恢复，增减节点或改变边前须确定图版本及接续策略。

展示继续使用 AgentEvent v1。`agent.message` 发送安全的简短状态说明，`tool.started` / `tool.finished` 跟踪追加检索，`evidence.added` 只发布确认入库的新 ID。评分卡与模型摘录留在内部记录。此阶段交付 Schema 和契约测试。

## EvaluationCase

`case_id` 标识离线用例，`brief: ResearchBrief` 提交给待测工作流，`question` 描述具体评测问题，`as_of` 固定参考时间。`reference_answer: list[Finding]` 按可核验事实拆分参考答案，并按需要保留争议或不足状态。`reference_evidence: list[Evidence]` 保存当时的摘录与出处。参考答案引用只在当前用例内解析。运行阶段只向待测流程传入公开调研简报和时间上下文，参考材料留给评分阶段。

- 确定性检查包括引用是否可以解析、来源是否重复、反思是否遵守轮次和停止条件。已知参考来源可按 `source_key` 比较，运行与用例各自分配的 Evidence ID 只用于各自数据集内的回溯。
- LLM 比较候选与参考结论是否语义等价、候选引用是否支持相应结论、有效替代来源以及必要的风险或不确定性说明。该调用独立于候选生成，输出逐项判断理由和可解析引用。未得到有效判断的项目记为评测不可用。
- 每个用例报告 `covered_reference_findings / reference_findings` 和 `valid_citations / citations`，两项都保留原始计数，零分母记为空。分别记录 `supported / insufficient / disputed` 的发现及停止原因，以便区分正确保留不确定性与漏答。数据集条目和评测版本由离线评测维护。
