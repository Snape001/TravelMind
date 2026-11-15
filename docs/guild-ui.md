# Guild UI Optimization

`/guild` 按领导层、专业专家层和执行层展示九位专家，卡片链接到 `/guild/[agentId]`。页面在桌面端使用多列公会卡片、并列的工作台面板和带粘性目录的报告布局，小视口下可折叠为单列。

## 档案与肖像

- [`frontend/src/lib/experts.ts`](../frontend/src/lib/experts.ts) 提供前端 `ExpertProfile` Fixture，以服务已有的 `agent_id` 为键。档案描述与工具能力是界面展示信息，运行中的工具调用仍以 SSE 事件为准。
- 九张肖像由内置 imagegen 逐张生成，保存到 [`frontend/public/images/experts/`](../frontend/public/images/experts/)，规格为 640 × 640 的 WebP。文件名是 `<agent_id>.webp`，使用正方形头肩与上身构图，保留头像裁切所需的头部留白。
- 历史任务仍可通过 `/guild/travel_planning` 打开旅游规划专家的原有档案。旅游规划专家及尚无档案的 Agent 使用图标头像。未登记的 Agent 显示原始 ID。

## 工作台与报告

工作台根据原有 AgentEvent v1 数据给事件、任务节点和分工条目附上档案身份。点击组织图节点会按原有 ID 筛选事件。

报告根据 `author_agent_ids` 显示头像、姓名和称号。共识和争议沿用报告原有章节，在章节标题显式标明共识或争议时应用对应样式。普通章节保留原有内容、位置和证据锚点。泉州示例报告第 2 版提供两种会商章节供预览。

## 肖像生成提示词

每条请求由下列共用提示词和对应专家的补充描述组成。

```text
Create exactly one square head-and-upper-torso portrait for a fictional Chinese travel research institute faculty directory (use case stylized-concept). Cohesive editorial series: refined realistic gouache and oil illustration on fine warm xuan paper, very subtle tactile grain, restrained Eastern scholarly aesthetic, professional and contemporary. Three-quarter shoulders and face turned gently toward camera, centered head with 10% top margin, head centered horizontally with eyes 39% down, upper chest visible, wide breathing room to preserve a circular avatar crop. Soft even side light, a flat muted parchment beige backdrop, minimal ink-teal/indigo/bronze accents, natural skin, simple tailored modern clothing with a subtle mandarin collar or structured lapel. No props, no scenery, no decorative frame, no character sheet, no text or watermark, no historical costume, no fantasy videogame look.
```

- `research_director` Cheng Ceyuan, male chief research director, mid 50s, dignified, brushed back salt-and-pepper hair, neat short greying moustache, dark ink-teal high-collar jacket.
- `history_culture` Shen Guanlan, female cultural historian, early 40s, soft approachable expression, dark hair pulled into a low bun, dark indigo jacket with a cream inner collar.
- `natural_geography` Gu Shanhe, male natural geographer, early 40s, sun-touched skin, very short textured hair, understated olive-grey field jacket with standing collar.
- `tourism_resources` Lu Zhixing, male visitor experience researcher, early 30s, short side-parted hair, clean shaven, open and observant, muted marine-blue chore jacket.
- `social_sentiment` Su Tingyu, female public sentiment researcher, early 30s, thoughtful and alert, straight black shoulder-length bob tucked behind one ear, dusty jade jacket with simple neckline.
- `literature_research` Wen Wenyuan, male literature researcher, early 60s, thin metal round glasses, gently greying cropped hair, chestnut-grey tailored jacket.
- `internet_research` Xie Xingzhou, male web researcher, late 20s, short wavy black hair, sharp attentive expression, slate-indigo structured overshirt.
- `report_writer` Ye Zhiqiu, female lead editor, early 40s, composed intent gaze, black hair in an understated low twist with part to the left, warm bronze-brown modern jacket.
- `reviewer` Liu Huaizhen, female review officer, early 50s, sober and confident expression, neat short salt-and-pepper bob, dark charcoal-blue tailored collared jacket.
