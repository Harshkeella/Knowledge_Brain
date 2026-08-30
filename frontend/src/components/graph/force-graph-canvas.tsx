"use client";

import dynamic from "next/dynamic";
import { useEffect, useLayoutEffect, useRef, type ComponentType, type RefObject } from "react";
import type { ForceGraphMethods as ForceGraphMethods2D } from "react-force-graph-2d";
import type { ForceGraphMethods as ForceGraphMethods3D } from "react-force-graph-3d";
import { symbolFor } from "@/constants/symbols";
import { ICON_MIN_RADIUS_PX, ICON_MIN_ZOOM, iconImage } from "./node-icons";

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

/**
 * The nodes handed to this canvas are the graph's own node objects, not
 * copies. react-force-graph stores each node's simulated x/y ON the object it
 * was given, so copying them per render throws the layout away and every hop
 * expansion would reset the whole view. Colour is therefore looked up rather
 * than baked on, which is what lets the same objects survive a re-render.
 */
export interface GraphCanvasNode {
  id: string;
  entity_type: string | null;
  source_type?: string | null;
  description: string | null;
  file_path: string | null;
  degree: number;
  x?: number;
  y?: number;
}

export interface GraphCanvasLink {
  source: string;
  target: string;
  keywords: string | null;
  edge_category?: string;
  description: string | null;
  weight: number | null;
}

/** Behavioral edges get their own colour: not one a node type already owns. */
const CALLS_COLOR = "#6366f1";
const OTHER_BEHAVIORAL_COLOR = "#8b8ad6";

/** Which node an expansion came from, and a counter so repeats still register. */
export interface Settle {
  id: string;
  seq: number;
}

type Simulated = GraphCanvasNode & { fx?: number; fy?: number };

/**
 * Layout effect, and it has to be: this is the only window where a pin still
 * beats the reheat.
 *
 * react-kapsule pushes changed props into the kapsule *during render*, so d3
 * has already been handed the new nodes and told to restart before any effect
 * runs. What saves this is that d3-force schedules its ticks on d3-timer, so
 * the first tick lands on the next animation frame -- and a layout effect is
 * still inside the same commit. A plain `useEffect` would also usually make
 * it, but "usually" is not something to build a layout on. Falls back on the
 * server, where there is no layout to be had.
 */
const useBeforePaint =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Hold every node that already has a place, so one expansion only moves the
 * nodes it revealed.
 *
 * d3 reheats the whole simulation whenever the data changes. Left alone, every
 * hop throws the settled graph back into motion and the map you just built
 * rearranges itself under you -- the single thing that makes progressive
 * exploration feel unusable. Pinned, the solver has only the arrivals left to
 * place; by the time `onEngineStop` releases them alpha is spent, so letting
 * go moves nothing. This lives here rather than in the explorer because these
 * are the simulation's own node objects: React hands them over and d3 mutates
 * them on every tick, so the pin belongs on the same side of that line.
 */
function useSettle(
  nodes: GraphCanvasNode[],
  settle: Settle | undefined,
  onSettled: (() => void) | undefined
) {
  const seen = useRef<Set<string>>(new Set());
  const pinned = useRef<Simulated[] | null>(null);
  const lastSeq = useRef(0);

  useBeforePaint(() => {
    if (settle && settle.seq !== lastSeq.current && seen.current.size) {
      lastSeq.current = settle.seq;
      const held = (nodes as Simulated[]).filter((n) => seen.current.has(n.id));
      for (const node of held) {
        node.fx = node.x;
        node.fy = node.y;
      }
      pinned.current = held;
    }
    seen.current = new Set(nodes.map((n) => n.id));
  }, [nodes, settle]);

  return () => {
    // Copied out of the ref before anything is touched: un-pinning is a
    // mutation, and these stop being ref state the moment the ref is cleared.
    const held: Simulated[] = Array.from(pinned.current ?? []);
    pinned.current = null;
    for (const node of held) {
      node.fx = undefined;
      node.fy = undefined;
    }
    onSettled?.();
  };
}

export function ForceGraphCanvas({
  mode,
  graphData,
  width,
  height,
  selectedId,
  colorFor,
  hiddenFor,
  settle,
  dimmed,
  dimmedLinks,
  onNodeClick,
  onBackgroundClick,
  graphRef,
}: {
  mode: "2d" | "3d";
  graphData: { nodes: GraphCanvasNode[]; links: GraphCanvasLink[] };
  width: number;
  height: number;
  selectedId: string | null;
  colorFor: (entityType: string | null) => string;
  /** Neighbours this node has that are not on the canvas yet. */
  hiddenFor?: (node: GraphCanvasNode) => number;
  /** The node the most recent expansion unfolded from. See useSettle. */
  settle?: Settle;
  dimmed: Set<string> | null;
  dimmedLinks?: (link: GraphCanvasLink) => boolean;
  onNodeClick: (id: string) => void;
  onBackgroundClick: () => void;
  graphRef: RefObject<ForceGraphRef | undefined>;
}) {
  const release = useSettle(graphData.nodes, settle, () => {
    // Keep whatever was just expanded within reach. Only when it has drifted
    // into the outer fifth: recentring a node that is already comfortably in
    // view is the camera moving for its own sake.
    const fg = graphRef.current as
      | {
          graph2ScreenCoords?: (x: number, y: number) => { x: number; y: number };
          centerAt?: (x: number, y: number, ms: number) => unknown;
        }
      | undefined;
    const origin = graphData.nodes.find((n) => n.id === settle?.id);
    if (mode !== "2d" || !origin || !fg?.graph2ScreenCoords || !fg.centerAt) return;
    const at = fg.graph2ScreenCoords(origin.x ?? 0, origin.y ?? 0);
    const m = 0.2;
    if (
      at.x < width * m ||
      at.x > width * (1 - m) ||
      at.y < height * m ||
      at.y > height * (1 - m)
    ) {
      fg.centerAt(origin.x ?? 0, origin.y ?? 0, 450);
    }
  });

  const nodeColor = (node: GraphCanvasNode) => {
    if (dimmed && dimmed.has(node.id)) return "rgba(137,135,129,0.25)";
    return colorFor(node.entity_type);
  };
  const nodeVal = (node: GraphCanvasNode) => 1.5 + Math.sqrt(node.degree + 1);
  const structuralColor = () =>
    document.documentElement.classList.contains("dark") ? "#383835" : "#c3c2b7";

  // Category, not relationship name: the frontend should not be re-deriving
  // what an edge means from a string it happens to recognise.
  const linkColor = (link: GraphCanvasLink) => {
    if (dimmedLinks?.(link)) return "rgba(137,135,129,0.08)";
    if (link.edge_category !== "behavioral") return structuralColor();
    return link.keywords === "CALLS" ? CALLS_COLOR : OTHER_BEHAVIORAL_COLOR;
  };
  const linkWidth = (link: GraphCanvasLink) => {
    if (link.edge_category === "behavioral") return link.keywords === "CALLS" ? 2 : 1.5;
    return link.weight ? Math.min(link.weight, 4) : 1;
  };
  // Structural edges are containment and read fine without arrows; a call has
  // a direction and is useless without one.
  const arrowLength = (link: GraphCanvasLink) =>
    link.edge_category === "behavioral" ? 5 : 0;
  const dashed = (link: GraphCanvasLink) =>
    link.edge_category === "behavioral" && link.keywords !== "CALLS" ? [4, 3] : [];

  const iconFor = (node: GraphCanvasNode) =>
    symbolFor(node.entity_type, node.source_type);

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
        linkWidth={linkWidth}
        linkDirectionalArrowLength={arrowLength}
        linkDirectionalArrowRelPos={1}
        linkOpacity={0.4}
        onNodeClick={(n: GraphCanvasNode) => onNodeClick(n.id)}
        onBackgroundClick={onBackgroundClick}
        onEngineStop={release}
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
      linkWidth={linkWidth}
      linkLineDash={dashed}
      linkDirectionalArrowLength={arrowLength}
      linkDirectionalArrowRelPos={1}
      linkDirectionalArrowColor={linkColor}
      onNodeClick={(n: GraphCanvasNode) => onNodeClick(n.id)}
      onBackgroundClick={onBackgroundClick}
      onEngineStop={release}
      nodeCanvasObjectMode={() => "after"}
      nodeCanvasObject={(
        node: GraphCanvasNode,
        ctx: CanvasRenderingContext2D,
        globalScale: number
      ) => {
        if (dimmed && dimmed.has(node.id)) return;
        const r = Math.sqrt(nodeVal(node)) * 4;
        const focused = node.id === selectedId;
        const x = node.x ?? 0;
        const y = node.y ?? 0;

        // A node that still has neighbours off-canvas wears a ring. When the
        // ring is gone the node is exhausted, and that is the only honest way
        // to know whether double-clicking again will do anything -- without it
        // hopping is guesswork and every leaf reads as a broken control.
        if ((hiddenFor?.(node) ?? 0) > 0) {
          ctx.save();
          ctx.beginPath();
          ctx.arc(x, y, r + 2.5, 0, Math.PI * 2);
          ctx.strokeStyle = colorFor(node.entity_type);
          ctx.globalAlpha = focused ? 0.85 : 0.4;
          ctx.lineWidth = (focused ? 2 : 1.5) / globalScale;
          ctx.stroke();
          ctx.restore();
        }

        // The glyph the legend shows, painted on the node itself. Gated on
        // apparent size: hundreds of overlapping icons at default zoom is
        // more clutter than the dots it replaced, not less. A focused node
        // always draws its icon, so you can identify what you are pointing at
        // inside a dense cluster.
        const spec = iconFor(node);
        if (
          spec &&
          (focused ||
            (globalScale >= ICON_MIN_ZOOM && r * globalScale >= ICON_MIN_RADIUS_PX))
        ) {
          const img = iconImage(spec.key, spec.iconNode);
          if (img) {
            const size = r * 1.15; // ~57% of the diameter
            ctx.drawImage(img, x - size / 2, y - size / 2, size, size);
          }
        }

        if (globalScale < 1.2 && !focused) return;
        const fontSize = 12 / globalScale;
        ctx.font = `${focused ? "bold " : ""}${fontSize}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const isDark = document.documentElement.classList.contains("dark");
        ctx.fillStyle = isDark ? "#ffffff" : "#0b0b0b";
        ctx.fillText(node.id, x, y + r + 4);
      }}
    />
  );
}
