"use client";

import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { ExpertAvatar } from "./expert-identity";
import { expertName, expertTitle, findExpert, tiers } from "@/lib/experts";
import type { summarize } from "@/lib/agent-events";

type AssignmentStatus = ReturnType<typeof summarize>["assignments"][number]["status"];
type TeamNode = Node<{ agentId: string; status: AssignmentStatus; selected: boolean }, "team">;
const width = 158;
const height = 72;
const directorId = "director";
const statusLabels = { waiting: "待执行", running: "执行中", completed: "已完成", failed: "失败" };

function AgentNode({ data }: NodeProps<TeamNode>) {
  const title = expertTitle(data.agentId);
  return (
    <div className={`team-node ${data.selected ? "is-selected" : ""}`} title={title ?? expertName(data.agentId)}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <ExpertAvatar id={data.agentId} />
      <div className="team-node-copy">
        <strong>{expertName(data.agentId)}</strong>
        {title && <small className="team-node-role">{title}</small>}
        <small><span className={`status-dot ${data.status}`} />{statusLabels[data.status]}</small>
      </div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { team: AgentNode };

export function TeamGraph({ assignments, selected, onSelect, directorStatus }: {
  assignments: ReturnType<typeof summarize>["assignments"];
  selected: string;
  onSelect: (agentId: string) => void;
  directorStatus: ReturnType<typeof summarize>["directorStatus"];
}) {
  const groups = [
    [{ agent_id: "research_director", assignment_id: directorId, status: directorStatus }],
    assignments.filter((assignment) => findExpert(assignment.agent_id)?.tier !== "editorial"),
    assignments.filter((assignment) => findExpert(assignment.agent_id)?.tier === "editorial"),
  ];
  const nodes: TeamNode[] = groups.flatMap((items, row) =>
    items.map((item, col) => ({
      id: item.assignment_id, type: "team", width, height, position: { x: (col - (items.length - 1) / 2) * (width + 16), y: row * 108 },
      data: { agentId: item.agent_id, status: item.status, selected: selected === item.agent_id },
      ariaLabel: [expertName(item.agent_id), expertTitle(item.agent_id), statusLabels[item.status], "筛选事件"].filter(Boolean).join("，"),
    })),
  );
  const edges: Edge[] = assignments.flatMap((item) => (
    item.depends_on?.length ? item.depends_on : [directorId]
  ).map((source) => ({
    id: `${source}-${item.assignment_id}`, source, target: item.assignment_id, type: "smoothstep",
    animated: item.status === "running", style: { stroke: item.status === "running" ? "var(--forest)" : "var(--node-edge)" },
    selectable: false, focusable: false,
  })));
  return (
    <>
      <div className="tier-key">{tiers.map((tier) => <span key={tier.id}>{tier.title}</span>)}</div>
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
    </>
  );
}
