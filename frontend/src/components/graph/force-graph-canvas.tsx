"use client";

import dynamic from "next/dynamic";
import type { ComponentType, RefObject } from "react";
import type { ForceGraphMethods as ForceGraphMethods2D } from "react-force-graph-2d";
import type { ForceGraphMethods as ForceGraphMethods3D } from "react-force-graph-3d";

// next/dynamic can't preserve react-force-graph's generic <NodeType, LinkType>
// signature, so the loaded components are typed loosely here; the props we
// pass below are still checked against our own GraphCanvasNode/Link types.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
}) as unknown as ComponentType<Record<string, unknown>>;
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), {
  ssr: false,
}) as unknown as ComponentType<Record<string, unknown>>;

export type ForceGraphRef = ForceGraphMethods2D | ForceGraphMethods3D;

export interface GraphCanvasNode {
  id: string;
  entity_type: string | null;
  description: string | null;
  file_path: string | null;
  degree: number;
  color: string;
}

export interface GraphCanvasLink {
  source: string;
  target: string;
  keywords: string | null;
  description: string | null;
  weight: number | null;
}

export function ForceGraphCanvas({
  mode,
  graphData,
  width,
  height,
  selectedId,
  dimmed,
  onNodeClick,
  onBackgroundClick,
  graphRef,
}: {
  mode: "2d" | "3d";
  graphData: { nodes: GraphCanvasNode[]; links: GraphCanvasLink[] };
  width: number;
  height: number;
  selectedId: string | null;
  dimmed: Set<string> | null;
  onNodeClick: (id: string) => void;
  onBackgroundClick: () => void;
  graphRef: RefObject<ForceGraphRef | undefined>;
}) {
  const nodeColor = (node: GraphCanvasNode) => {
    if (dimmed && dimmed.has(node.id)) return "rgba(137,135,129,0.25)";
    return node.color;
  };
  const nodeVal = (node: GraphCanvasNode) => 1.5 + Math.sqrt(node.degree + 1);
  const linkColor = () =>
    document.documentElement.classList.contains("dark") ? "#383835" : "#c3c2b7";

  if (mode === "3d") {
    return (
      <ForceGraph3D
        ref={graphRef}
        graphData={graphData}
        width={width}
        height={height}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        nodeLabel={(n: GraphCanvasNode) => `${n.id}${n.entity_type ? ` (${n.entity_type})` : ""}`}
        nodeVal={nodeVal}
        nodeColor={nodeColor}
        nodeOpacity={0.95}
        linkColor={linkColor}
        linkWidth={(l: GraphCanvasLink) => (l.weight ? Math.min(l.weight, 4) : 1)}
        linkOpacity={0.4}
        onNodeClick={(n: GraphCanvasNode) => onNodeClick(n.id)}
        onBackgroundClick={onBackgroundClick}
      />
    );
  }

  return (
    <ForceGraph2D
      ref={graphRef}
      graphData={graphData}
      width={width}
      height={height}
      backgroundColor="rgba(0,0,0,0)"
      nodeId="id"
      nodeLabel={(n: GraphCanvasNode) => `${n.id}${n.entity_type ? ` (${n.entity_type})` : ""}`}
      nodeVal={nodeVal}
      nodeColor={nodeColor}
      linkColor={linkColor}
      linkWidth={(l: GraphCanvasLink) => (l.weight ? Math.min(l.weight, 4) : 1)}
      linkDirectionalArrowLength={3}
      linkDirectionalArrowRelPos={1}
      onNodeClick={(n: GraphCanvasNode) => onNodeClick(n.id)}
      onBackgroundClick={onBackgroundClick}
      nodeCanvasObjectMode={() => "after"}
      nodeCanvasObject={(
        node: GraphCanvasNode & { x?: number; y?: number },
        ctx: CanvasRenderingContext2D,
        globalScale: number
      ) => {
        if (dimmed && dimmed.has(node.id)) return;
        if (globalScale < 1.2 && node.id !== selectedId) return;
        const label = node.id;
        const fontSize = 12 / globalScale;
        ctx.font = `${node.id === selectedId ? "bold " : ""}${fontSize}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const isDark = document.documentElement.classList.contains("dark");
        ctx.fillStyle = isDark ? "#ffffff" : "#0b0b0b";
        const r = Math.sqrt(nodeVal(node)) * 4;
        ctx.fillText(label, node.x ?? 0, (node.y ?? 0) + r + 1);
      }}
    />
  );
}
