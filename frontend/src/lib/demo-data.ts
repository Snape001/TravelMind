import type { AgentEvent, EvidenceRead, ReportRead, TaskRead } from "./research";

type DemoResearch = { task: TaskRead; events: AgentEvent[]; evidence: EvidenceRead[]; reports: ReportRead[] };

export const demoResearch: DemoResearch = {
  "task": {
    "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
    "trace_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c002",
    "brief": {
      "destination": "泉州",
      "topics": [
        "古城文化",
        "公共交通"
      ],
      "requirements": "周末两天，以步行和公共交通为主。",
      "depth": "deep"
    },
    "status": "completed",
    "created_at": "2026-11-14T02:00:00.000Z",
    "updated_at": "2026-11-14T02:00:15.700Z"
  },
  "events": [
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000013","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":1,"occurred_at":"2026-11-14T02:00:00.000Z","payload":{"type":"task.status","status":"queued"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000014","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":2,"occurred_at":"2026-11-14T02:00:00.450Z","payload":{"type":"task.status","status":"running"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000015","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":3,"occurred_at":"2026-11-14T02:00:00.650Z","payload":{"type":"agent.started","agent_id":"research_director","run_id":"856d05aa-7d53-4fe7-aefa-000000000012"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000016","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":4,"occurred_at":"2026-11-14T02:00:01.100Z","payload":{"type":"agent.message","agent_id":"research_director","run_id":"856d05aa-7d53-4fe7-aefa-000000000012","message":"已收到关于泉州的调研需求，将按「古城文化、公共交通」组织专家分工。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000017","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":5,"occurred_at":"2026-11-14T02:00:01.800Z","payload":{"type":"plan.ready","plan":{"objective":"围绕古城文化、公共交通形成泉州的可追溯调研报告。","assignments":[{"assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000a","agent_id":"history_culture","objective":"从历史文化角度梳理泉州的相关资料与待核信息。"},{"assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000b","agent_id":"travel_planning","objective":"从旅游规划角度梳理泉州的相关资料与待核信息。"},{"assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000c","agent_id":"internet_research","objective":"从互联网研究角度梳理泉州的相关资料与待核信息。"},{"assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000d","agent_id":"report_writer","objective":"整合研究成果并生成报告。","depends_on":["856d05aa-7d53-4fe7-aefa-00000000000a","856d05aa-7d53-4fe7-aefa-00000000000b","856d05aa-7d53-4fe7-aefa-00000000000c"]},{"assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000e","agent_id":"reviewer","objective":"核对引用与适用条件。","depends_on":["856d05aa-7d53-4fe7-aefa-00000000000d"]}]}}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000018","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":6,"occurred_at":"2026-11-14T02:00:02.000Z","payload":{"type":"agent.finished","agent_id":"research_director","run_id":"856d05aa-7d53-4fe7-aefa-000000000012","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000001f","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":7,"occurred_at":"2026-11-14T02:00:02.400Z","payload":{"type":"agent.started","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000a"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000020","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":8,"occurred_at":"2026-11-14T02:00:02.520Z","payload":{"type":"agent.started","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000b"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000021","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":9,"occurred_at":"2026-11-14T02:00:02.640Z","payload":{"type":"agent.started","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000c"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000022","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":10,"occurred_at":"2026-11-14T02:00:03.250Z","payload":{"type":"tool.started","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001c","tool_name":"网页资料检索"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000023","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":11,"occurred_at":"2026-11-14T02:00:03.420Z","payload":{"type":"tool.started","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001d","tool_name":"交通资料检索"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000024","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":12,"occurred_at":"2026-11-14T02:00:03.590Z","payload":{"type":"tool.started","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001e","tool_name":"网页资料检索"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000025","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":13,"occurred_at":"2026-11-14T02:00:04.500Z","payload":{"type":"tool.finished","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001c","status":"completed","duration_ms":1250}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000026","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":14,"occurred_at":"2026-11-14T02:00:04.620Z","payload":{"type":"evidence.added","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","evidence_ids":["856d05aa-7d53-4fe7-aefa-00000000000f"]}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000027","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":15,"occurred_at":"2026-11-14T02:00:04.780Z","payload":{"type":"agent.message","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","message":"我整理了泉州的第一组资料。请其他专家核对这些线索的适用季节与出行条件。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000028","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":16,"occurred_at":"2026-11-14T02:00:04.930Z","payload":{"type":"agent.finished","agent_id":"history_culture","run_id":"856d05aa-7d53-4fe7-aefa-000000000019","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000029","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":17,"occurred_at":"2026-11-14T02:00:05.090Z","payload":{"type":"tool.finished","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001d","status":"completed","duration_ms":1670}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002a","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":18,"occurred_at":"2026-11-14T02:00:05.210Z","payload":{"type":"evidence.added","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","evidence_ids":["856d05aa-7d53-4fe7-aefa-000000000010"]}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002b","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":19,"occurred_at":"2026-11-14T02:00:05.370Z","payload":{"type":"agent.message","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","message":"收到。会在结论中标明资料适用范围，并把实时开放和预约信息列为待核事项。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002c","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":20,"occurred_at":"2026-11-14T02:00:05.520Z","payload":{"type":"agent.finished","agent_id":"travel_planning","run_id":"856d05aa-7d53-4fe7-aefa-00000000001a","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002d","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":21,"occurred_at":"2026-11-14T02:00:05.680Z","payload":{"type":"tool.finished","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","tool_call_id":"856d05aa-7d53-4fe7-aefa-00000000001e","status":"completed","duration_ms":2090}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002e","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":22,"occurred_at":"2026-11-14T02:00:05.800Z","payload":{"type":"evidence.added","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","evidence_ids":["856d05aa-7d53-4fe7-aefa-000000000011"]}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000002f","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":23,"occurred_at":"2026-11-14T02:00:05.960Z","payload":{"type":"agent.message","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","message":"收到。会在结论中标明资料适用范围，并把实时开放和预约信息列为待核事项。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000030","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":24,"occurred_at":"2026-11-14T02:00:06.110Z","payload":{"type":"agent.finished","agent_id":"internet_research","run_id":"856d05aa-7d53-4fe7-aefa-00000000001b","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000035","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":25,"occurred_at":"2026-11-14T02:00:07.700Z","payload":{"type":"agent.started","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000031","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000d"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000036","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":26,"occurred_at":"2026-11-14T02:00:08.800Z","payload":{"type":"agent.message","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000031","message":"正在合并专家结论，为每个章节附上署名和引用。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000037","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":27,"occurred_at":"2026-11-14T02:00:09.900Z","payload":{"type":"report.ready","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000031","report_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c003","version":1}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000038","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":28,"occurred_at":"2026-11-14T02:00:10.100Z","payload":{"type":"agent.finished","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000031","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000039","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":29,"occurred_at":"2026-11-14T02:00:10.400Z","payload":{"type":"agent.started","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000032","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000e"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003a","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":30,"occurred_at":"2026-11-14T02:00:11.100Z","payload":{"type":"agent.message","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000032","message":"请补充出发前应核实的开放时间、预约规则和交通条件。"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003b","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":31,"occurred_at":"2026-11-14T02:00:11.400Z","payload":{"type":"report.reviewed","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000032","report_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c003","decision":"revision_required"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003c","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":32,"occurred_at":"2026-11-14T02:00:11.600Z","payload":{"type":"agent.finished","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000032","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003d","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":33,"occurred_at":"2026-11-14T02:00:12.200Z","payload":{"type":"agent.started","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000033","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000d"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003e","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":34,"occurred_at":"2026-11-14T02:00:13.900Z","payload":{"type":"report.ready","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000033","report_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c004","version":2}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-00000000003f","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":35,"occurred_at":"2026-11-14T02:00:14.100Z","payload":{"type":"agent.finished","agent_id":"report_writer","run_id":"856d05aa-7d53-4fe7-aefa-000000000033","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000040","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":36,"occurred_at":"2026-11-14T02:00:14.500Z","payload":{"type":"agent.started","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000034","assignment_id":"856d05aa-7d53-4fe7-aefa-00000000000e"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000041","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":37,"occurred_at":"2026-11-14T02:00:15.200Z","payload":{"type":"report.reviewed","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000034","report_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c004","decision":"approved"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000042","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":38,"occurred_at":"2026-11-14T02:00:15.400Z","payload":{"type":"agent.finished","agent_id":"reviewer","run_id":"856d05aa-7d53-4fe7-aefa-000000000034","status":"completed"}},
    {"version":1,"event_id":"856d05aa-7d53-4fe7-aefa-000000000043","task_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c001","trace_id":"856d05aa-7d53-4fe7-aefa-d17e6a84c002","sequence":39,"occurred_at":"2026-11-14T02:00:15.700Z","payload":{"type":"task.status","status":"completed"}}
  ],
  "evidence": [
    {
      "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
      "evidence_id": "856d05aa-7d53-4fe7-aefa-00000000000f",
      "source_type": "official",
      "source_title": "联合国教科文组织 · 泉州世界遗产档案",
      "source_url": "https://whc.unesco.org/en/list/1561/",
      "source_locator": "UNESCO World Heritage List, no. 1561",
      "retrieved_at": "2026-11-14T02:00:04.000Z",
      "excerpt": "遗产档案介绍了宋元时期泉州海洋贸易相关的遗产点。"
    },
    {
      "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
      "evidence_id": "856d05aa-7d53-4fe7-aefa-000000000010",
      "source_type": "web",
      "source_title": "泉州 · 旅游规划专家资料样例",
      "source_url": null,
      "source_locator": "模拟资料样例 2",
      "retrieved_at": "2026-11-14T02:00:04.000Z",
      "excerpt": "演示摘录。真实调研时需补充泉州的原始资料、发布时间及适用条件。"
    },
    {
      "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
      "evidence_id": "856d05aa-7d53-4fe7-aefa-000000000011",
      "source_type": "web",
      "source_title": "泉州 · 互联网研究专家资料样例",
      "source_url": null,
      "source_locator": "模拟资料样例 3",
      "retrieved_at": "2026-11-14T02:00:04.000Z",
      "excerpt": "演示摘录。真实调研时需补充泉州的原始资料、发布时间及适用条件。"
    }
  ],
  "reports": [
    {
      "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
      "report_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c003",
      "version": 1,
      "created_at": "2026-11-14T02:00:09.900Z",
      "content": {
        "title": "泉州古城文化与周末出行调研",
        "sections": [
          {
            "heading": "调研概览",
            "author_agent_ids": [
              "research_director"
            ],
            "body": "本次围绕泉州的古城文化、公共交通展开模拟调研。出行需求为「周末两天，以步行和公共交通为主。」。\n\n报告展示专家分工、结论组织和证据追溯的完整流程。"
          },
          {
            "heading": "古城文化与遗产线索",
            "author_agent_ids": [
              "history_culture",
              "travel_planning"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-00000000000f",
              "856d05aa-7d53-4fe7-aefa-000000000010"
            ],
            "body": "可从宋元时期的海上贸易切入，结合 UNESCO 遗产档案了解泉州的遗产点。周末行程可以按主题选取少量地点，为步行和现场参观预留时间。"
          },
          {
            "heading": "行程安排与待核事项",
            "author_agent_ids": [
              "report_writer",
              "internet_research"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-000000000011"
            ],
            "body": "先选定主要游览区域，再按步行与公共交通的可达性安排备选地点。当前示例中的实时开放、交通与预约信息仍待核实。"
          }
        ]
      }
    },
    {
      "task_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c001",
      "report_id": "856d05aa-7d53-4fe7-aefa-d17e6a84c004",
      "version": 2,
      "created_at": "2026-11-14T02:00:13.900Z",
      "content": {
        "title": "泉州古城文化与周末出行调研",
        "sections": [
          {
            "heading": "调研概览",
            "author_agent_ids": [
              "research_director"
            ],
            "body": "本次围绕泉州的古城文化、公共交通展开模拟调研。出行需求为「周末两天，以步行和公共交通为主。」。\n\n报告展示专家分工、结论组织和证据追溯的完整流程。"
          },
          {
            "heading": "古城文化与遗产线索",
            "author_agent_ids": [
              "history_culture",
              "travel_planning"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-00000000000f",
              "856d05aa-7d53-4fe7-aefa-000000000010"
            ],
            "body": "可从宋元时期的海上贸易切入，结合 UNESCO 遗产档案了解泉州的遗产点。周末行程可以按主题选取少量地点，为步行和现场参观预留时间。"
          },
          {
            "heading": "行程安排与待核事项",
            "author_agent_ids": [
              "report_writer",
              "internet_research"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-000000000011"
            ],
            "body": "先选定主要游览区域，再按步行与公共交通的可达性安排备选地点。当前示例中的实时开放、交通与预约信息仍待核实。\n\n审核修订补充了出发前检查项：开放时间、预约规则和末班交通。"
          },
          {
            "heading": "专家共识",
            "author_agent_ids": [
              "history_culture",
              "internet_research"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-00000000000f",
              "856d05aa-7d53-4fe7-aefa-000000000011"
            ],
            "body": "示例会商中，两位专家同意以联合国教科文组织遗产档案确定文化调研范围，并在出发前核验景点公告。"
          },
          {
            "heading": "专家争议",
            "author_agent_ids": [
              "history_culture",
              "travel_planning"
            ],
            "evidence_ids": [
              "856d05aa-7d53-4fe7-aefa-00000000000f",
              "856d05aa-7d53-4fe7-aefa-000000000010"
            ],
            "body": "示例会商中，历史文化专家建议按主题串联遗产点，旅游规划专家建议先按步行距离筛选。两种方案的可行性取决于开放时段和班次，示例资料尚不足以确认哪种更合适。"
          }
        ]
      }
    }
  ]
};
