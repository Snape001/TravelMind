"use client";

import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { ChevronRight, GitBranch, Layers3 } from "lucide-react";
import { ExpertAvatar } from "./expert-identity";
import { canonicalAgentId, expertName, expertTitle, findExpert, tiers } from "@/lib/experts";
import type { summarize } from "@/lib/agent-events";
import type { UsageFor } from "@/lib/research-browser";
import { UsageHover } from "./usage-hover";

type View = ReturnType<typeof summarize>;
type AssignmentStatus = View["assignments"][number]["status"];
type GraphAssignment = Pick<View["assignments"][number], "agent_id" | "assignment_id" | "depends_on" | "status">;
type TeamNode = Node<{ agentId: string; status: AssignmentStatus; selected: boolean }, "team">;
const width = 158;
const height = 72;
const directorId = "director";
export const assignmentLabels = { waiting: "待执行", running: "执行中", completed: "已完成", failed: "失败" };
const editorial = [{ agentId: "report_writer", label: "Writer · 撰写" }, { agentId: "reviewer", label: "Reviewer · 审核" }];

function AgentNode({ data }: NodeProps<TeamNode>) {
  const title = expertTitle(data.agentId);
  return (
    <div className={`team-node ${data.selected ? "is-selected" : ""}`} title={title ?? expertName(data.agentId)}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <ExpertAvatar id={data.agentId} />
      <div className="team-node-copy">
        <strong>{expertName(data.agentId)}</strong>
        {title && <small className="team-node-role">{title}</small>}
        <small><span className={`status-dot ${data.status}`} />{assignmentLabels[data.status]}</small>
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { team: AgentNode };

export function TeamGraph({ view, selected, onSelect, usageFor }: {
  view: View;
  selected: string;
  onSelect: (agentId: string) => void;
  usageFor: UsageFor;
}) {
  const [leadership, council, execution] = tiers;
  const ids = [...new Set(["research_director", ...view.assignments.map(({ agent_id }) => canonicalAgentId(agent_id)),
    ...view.agents.keys(), ...editorial.map(({ agentId }) => agentId)])];
  const groups = [leadership, council, execution].map((tier) => ({
    ...tier, ids: ids.filter((id) => (findExpert(id)?.tier ?? "council") === tier.id),
  }));
  return <>
    <button type="button" className="guild-filter" aria-pressed={!selected} onClick={() => onSelect("")}>
      <Layers3 size={16} aria-hidden /> 全部专家 <ChevronRight size={14} aria-hidden />
    </button>
    <nav className="mini-workflow" aria-label="三级工作流">
      {groups.map(({ id, title, ids }) => <section key={id} className={`workflow-tier ${id}`}>
        <h3>{title}</h3>
        {ids.length ? <ul>{ids.map((agentId) => {
          const agent = view.agents.get(agentId);
          const status = agent?.status ?? view.assignments.find(({ agent_id }) => canonicalAgentId(agent_id) === agentId)?.status ?? "waiting";
          const role = editorial.find((entry) => entry.agentId === agentId)?.label ?? expertTitle(agentId);
          const name = expertName(agentId);
          return <li key={agentId}><UsageHover calls={agent ? usageFor(agentId, agent.runId) : []} label={name} className="workflow-usage">
            <button type="button" className="workflow-agent" aria-pressed={selected === agentId}
              onClick={() => onSelect(selected === agentId ? "" : agentId)}>
              <ExpertAvatar id={agentId} />
              <span className="workflow-agent-name"><strong>{name}</strong>{role && <small>{role}</small>}</span>
              <span className="workflow-status"><span className={`status-dot ${status}`} />{assignmentLabels[status]}</span>
            </button>
          </UsageHover></li>;
        })}</ul> : <p className="muted workflow-empty">等待专家分工</p>}
      </section>)}
    </nav>
    <details className="workflow-details">
      <summary><GitBranch size={15} aria-hidden />查看任务依赖图</summary>
      <DependencyGraph view={view} selected={selected} onSelect={onSelect} />
    </details>
  </>;
}

function DependencyGraph({ view, selected, onSelect }: {
  view: View;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const assignments = view.assignments.map((item) => ({ ...item, agent_id: canonicalAgentId(item.agent_id) }));
  const mainAssignments = assignments.filter((item) => findExpert(item.agent_id)?.tier !== "editorial");
  const editorialAssignments = editorial.flatMap<GraphAssignment>(({ agentId }) => {
    const planned = assignments.filter(({ agent_id }) => agent_id === agentId);
    return planned.length ? planned : [{ agent_id: agentId, assignment_id: agentId, depends_on: [],
      status: view.agents.get(agentId)?.status ?? "waiting" }];
  });
  const groups = [
    [{ agent_id: "research_director", assignment_id: directorId, status: view.directorStatus }],
    mainAssignments,
    editorialAssignments,
  ];
  const nodes: TeamNode[] = groups.flatMap((items, row) =>
    items.map((item, col) => ({
      id: item.assignment_id, type: "team", width, height, position: { x: (col - (items.length - 1) / 2) * (width + 16), y: row * 108 },
      data: { agentId: item.agent_id, status: item.status, selected: selected === item.agent_id },
      ariaLabel: [expertName(item.agent_id), expertTitle(item.agent_id), assignmentLabels[item.status], "筛选事件"].filter(Boolean).join("，"),
    })),
  );
  const writerNodes = editorialAssignments.filter(({ agent_id }) => agent_id === "report_writer");
  const edges: Edge[] = [...mainAssignments, ...editorialAssignments].flatMap((item) => {
    const dependencies = item.depends_on?.length ? item.depends_on :
      item.agent_id === "reviewer" ? writerNodes.map(({ assignment_id }) => assignment_id) :
        item.agent_id === "report_writer" && mainAssignments.length ? mainAssignments.map(({ assignment_id }) => assignment_id) : [directorId];
    return dependencies.map((source) => ({
      id: `${source}-${item.assignment_id}`, source, target: item.assignment_id, type: "smoothstep",
      animated: item.status === "running", style: { stroke: item.status === "running" ? "var(--forest)" : "var(--node-edge)" },
      selectable: false, focusable: false,
    }));
  });
  return (
      <div className="team-graph" role="group" aria-label="三级组织架构与任务依赖">
        <ReactFlow<TeamNode> key={nodes.map(({ id }) => id).join(",")}
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.12, maxZoom: 1.1 }}
          minZoom={0.2} maxZoom={1.6} nodesDraggable={false} nodesConnectable={false} zoomOnScroll={false}
          panOnScroll={false} onNodeClick={(_, node) => onSelect(node.data.agentId)}
          onSelectionChange={({ nodes: selectedNodes }) => {
            if (selectedNodes[0]) onSelect(selectedNodes[0].data.agentId);
          }}
        >
          <Background gap={18} color="var(--line)" />
          <Controls showInteractive={false} aria-label="组织图缩放" />
        </ReactFlow>
      </div>
  );
}
