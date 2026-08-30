"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  expandNode,
  getGraph,
  getSources,
  listKnowledgeBase,
  type Graph,
  type GraphEdge,
  type GraphNode,
} from "@/lib/api";
import { CODE_ENTITY_TYPES, symbolFor } from "@/constants/symbols";
import { buildEntityColorScale } from "./entity-colors";
import {
  ForceGraphCanvas,
  type ForceGraphRef,
  type Settle,
} from "./force-graph-canvas";
import { warmIconCache } from "./node-icons";

/** The readable tail of a node id, for status copy that has to fit on a line. */
function label(nodeId: string): string {
  return nodeId.split("::").pop()?.split("/").pop() || nodeId;
}

/** A node once the force simulation has given it coordinates. */
type Placed = GraphNode & { x?: number; y?: number };

/** Where the arrivals of one expansion are laid out, in graph units. */
const HOP_RADIUS = 72;
/** Golden angle: successive expansions of one node never overlap their arcs. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export function GraphExplorer() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /**
   * "hop" is the landing state: Source nodes only, one per ingestion, and you
   * double-click your way down. This is not a smaller version of the full
   * graph -- it is the only view that can reach a folder eight levels deep,
   * because the full load is a degree-capped BFS and everything past its
   * horizon is unreachable no matter how far you zoom. "full" is that old
   * dense view, unchanged and one button away.
   */
  const [viewMode, setViewMode] = useState<"hop" | "full">("hop");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [expanding, setExpanding] = useState<string | null>(null);
  /**
   * What the last hop did, shown over the canvas and then dismissed.
   *
   * A failed or empty expansion used to raise the page-level `error`, which
   * replaced the whole explorer — losing an exploration twenty hops deep
   * because one fetch failed. A hop is a local action and reports locally.
   */
  const [notice, setNotice] = useState<{ tone: "info" | "error"; text: string } | null>(
    null
  );
  const [settle, setSettle] = useState<Settle | undefined>(undefined);
  const [mode, setMode] = useState<"2d" | "3d">("2d");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // A folder tree, or a workbook's structure, is a handful of low-degree nodes
  // that never survives the top-degree cut of the whole-graph view. Centering
  // on that upload's supernode is how you actually see it -- and it is also
  // what keeps supernodes (which are hubs by design) from dominating the `*`
  // view: they are opt-in, not the default.
  const [focus, setFocus] = useState(() => {
    // Read straight off the URL rather than useSearchParams: that hook forces
    // the whole page under a Suspense boundary for no benefit here.
    if (typeof window === "undefined") return "*";
    return new URLSearchParams(window.location.search).get("focus") || "*";
  });
  const [sources, setSources] = useState<{ id: string; label: string }[]>([]);
  // Hides everything that is not code, so the call graph can be read on its
  // own instead of through a cloud of people, dates and organizations.
  const [codeOnly, setCodeOnly] = useState(false);

  const clickRef = useRef<{ id: string; at: number }>({ id: "", at: 0 });

  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphRef | undefined>(undefined);
  const [size, setSize] = useState({ width: 800, height: 600 });

  useEffect(() => {
    let cancelled = false;
    const load =
      viewMode === "hop"
        ? getSources()
        : getGraph({ maxNodes: 300, label: focus, maxDepth: focus === "*" ? 3 : 4 });
    load
      .then((g) => {
        if (cancelled) return;
        setGraph(g);
        setExpanded(new Set());
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [focus, viewMode]);

  useEffect(() => {
    listKnowledgeBase()
      .then((docs) =>
        setSources(
          docs.map((d) => ({
            id: `source:${d.file_name}`,
            label: `${d.file_name} (${d.source_type})`,
          }))
        )
      )
      .catch(() => setSources([]));
  }, []);

  // Decode the glyphs before the first paint rather than a frame or two into
  // the force simulation.
  useEffect(() => {
    warmIconCache();
  }, []);

  // An error is something to read and act on; "nothing further" is something
  // to register and forget. Only the transient one expires on its own.
  useEffect(() => {
    if (notice?.tone !== "info") return;
    const id = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(id);
  }, [notice]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({
        width: Math.max(entry.contentRect.width, 200),
        height: Math.max(entry.contentRect.height, 200),
      });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const view = useMemo(() => {
    if (!graph) return null;
    if (!codeOnly) return graph;
    const nodes = graph.nodes.filter(
      (n) =>
        CODE_ENTITY_TYPES.has((n.entity_type ?? "").toLowerCase()) ||
        // Sources are the hop view's only way in; filtering them out leaves a
        // blank canvas with nothing left to double-click.
        (viewMode === "hop" && n.entity_type === "source")
    );
    const ids = new Set(nodes.map((n) => n.id));
    return {
      ...graph,
      nodes,
      // Semantic edges go with the entities they connected; an edge to a node
      // that is no longer drawn is a line into empty space.
      edges: graph.edges.filter(
        (e) =>
          e.edge_category !== "semantic" && ids.has(e.source) && ids.has(e.target)
      ),
    };
  }, [graph, codeOnly, viewMode]);

  const colorScale = useMemo(
    () => (view ? buildEntityColorScale(view.nodes) : null),
    [view]
  );

  const canvasData = useMemo(() => {
    if (!view) return { nodes: [], links: [] };
    return {
      // The graph's own node objects, deliberately not copies: see
      // GraphCanvasNode. An expansion appends to this array, so every node
      // already on screen keeps its identity and therefore its position.
      nodes: view.nodes,
      links: view.edges.map((e) => ({
        source: e.source,
        target: e.target,
        keywords: e.keywords,
        edge_category: e.edge_category,
        description: e.description,
        weight: e.weight,
      })),
    };
  }, [view]);

  /**
   * How many neighbours each node still has off-canvas: its true degree in the
   * store, minus the edges currently drawn on it.
   *
   * `/graph/sources` and `/graph/expand` report the real store degree, which
   * is what makes this answerable without another round trip. In the full
   * view `degree` is already the drawn degree, so this is uniformly zero and
   * no rings appear — correct, since that view is not something you hop
   * through.
   */
  const hiddenCounts = useMemo(() => {
    if (!view) return new Map<string, number>();
    const drawn = new Map<string, number>();
    for (const e of view.edges) {
      drawn.set(e.source, (drawn.get(e.source) ?? 0) + 1);
      drawn.set(e.target, (drawn.get(e.target) ?? 0) + 1);
    }
    return new Map(
      view.nodes.map((n) => [n.id, Math.max(0, n.degree - (drawn.get(n.id) ?? 0))])
    );
  }, [view]);

  const selectedNode = useMemo(
    () => view?.nodes.find((n) => n.id === selectedId) ?? null,
    [view, selectedId]
  );

  const selectedEdges = useMemo(() => {
    if (!view || !selectedId) return [];
    return view.edges.filter(
      (e) => e.source === selectedId || e.target === selectedId
    );
  }, [view, selectedId]);

  /** Merge one hop's worth of nodes/edges in, deduped by id. */
  const hop = useCallback(
    async (nodeId: string) => {
      if (!view) return;
      // The ring already said this node has nothing left. Answering from what
      // is on screen beats a round trip that can only confirm it.
      if (viewMode === "hop" && hiddenCounts.get(nodeId) === 0) {
        setNotice({ tone: "info", text: `Nothing further from ${label(nodeId)}.` });
        return;
      }

      setExpanding(nodeId);
      setNotice(null);
      try {
        const delta = await expandNode(nodeId);
        const onScreen = new Set(view.nodes.map((n) => n.id));
        // A node reached twice, by two different paths, is still one node:
        // only the new edge is added.
        const fresh = delta.nodes.filter((n) => !onScreen.has(n.id));
        const origin = view.nodes.find((n) => n.id === nodeId) as Placed | undefined;

        // Arrivals land on an arc around whatever revealed them, evenly spaced
        // rather than jittered: the ring reads as "these came from here", and
        // the golden angle keeps a second expansion of the same node off the
        // first one's spokes.
        fresh.forEach((node, i) => {
          const angle = i * GOLDEN_ANGLE + fresh.length;
          Object.assign(node, {
            x: (origin?.x ?? 0) + Math.cos(angle) * HOP_RADIUS,
            y: (origin?.y ?? 0) + Math.sin(angle) * HOP_RADIUS,
          });
        });

        // Re-deduped against whatever state actually holds now, not against
        // the snapshot above: two expansions can be in flight at once.
        setGraph((current) => {
          if (!current) return current;
          const seenNodes = new Set(current.nodes.map((n) => n.id));
          const seenEdges = new Set(current.edges.map((e) => e.id));
          return {
            ...current,
            nodes: [...current.nodes, ...fresh.filter((n) => !seenNodes.has(n.id))],
            edges: [
              ...current.edges,
              ...delta.edges.filter((e) => !seenEdges.has(e.id)),
            ],
          };
        });
        setExpanded((current) => new Set(current).add(nodeId));
        // Tells the canvas to hold everything already placed while these land.
        setSettle((s) => ({ id: nodeId, seq: (s?.seq ?? 0) + 1 }));
        if (!delta.nodes.length) {
          setNotice({ tone: "info", text: `Nothing further from ${label(nodeId)}.` });
        }
      } catch (e) {
        setNotice({
          tone: "error",
          text: `Could not expand ${label(nodeId)}: ${
            e instanceof Error ? e.message : String(e)
          }`,
        });
      } finally {
        setExpanding(null);
      }
    },
    [view, viewMode, hiddenCounts]
  );

  // react-force-graph exposes onNodeClick and onNodeRightClick and nothing
  // else, so the double-click is timed here rather than handed to it.
  const handleNodeClick = useCallback(
    (id: string) => {
      const now = Date.now();
      const double = clickRef.current.id === id && now - clickRef.current.at < 400;
      clickRef.current = { id, at: now };
      setSelectedId(id);
      if (double) void hop(id);
    },
    [hop]
  );

  /**
   * The call neighbourhood of the selected code node. This is the answer to
   * "who calls this": the direction is already correct on the edge (the graph
   * store is undirected, so the backend carries rel_from/rel_to), so callers
   * and callees are two filters rather than a traversal.
   */
  const trace = useMemo(() => {
    if (!selectedNode) return null;
    if (!CODE_ENTITY_TYPES.has((selectedNode.entity_type ?? "").toLowerCase()))
      return null;
    const calls = selectedEdges.filter((e) => e.keywords === "CALLS");
    const out = calls.filter((e) => e.source === selectedNode.id);
    const incoming = calls.filter((e) => e.target === selectedNode.id);
    if (!out.length && !incoming.length) return null;
    return {
      out,
      incoming,
      keep: new Set([
        selectedNode.id,
        ...out.map((e) => e.target),
        ...incoming.map((e) => e.source),
      ]),
      edgeIds: new Set(calls.map((e) => e.id)),
    };
  }, [selectedNode, selectedEdges]);

  const dimmed = useMemo(() => {
    if (!view) return null;
    // Tracing a call chain wins over the search dim: it is the more specific
    // thing the user just asked for.
    if (trace) {
      const dim = new Set<string>();
      for (const n of view.nodes) if (!trace.keep.has(n.id)) dim.add(n.id);
      return dim;
    }
    const q = search.trim().toLowerCase();
    if (!q) return null;
    const matched = new Set(
      view.nodes.filter((n) => n.id.toLowerCase().includes(q)).map((n) => n.id)
    );
    const keep = new Set(matched);
    for (const e of view.edges) {
      if (matched.has(e.source)) keep.add(e.target);
      if (matched.has(e.target)) keep.add(e.source);
    }
    const dim = new Set<string>();
    for (const n of view.nodes) if (!keep.has(n.id)) dim.add(n.id);
    return dim;
  }, [search, view, trace]);

  const dimmedLinks = useMemo(() => {
    if (!trace) return undefined;
    const lit = new Set(
      [...trace.out, ...trace.incoming].map((e) => `${e.source}\u0000${e.target}`)
    );
    return (link: { source: unknown; target: unknown }) => {
      // react-force-graph swaps the id for the node object once it has laid
      // the graph out, so both shapes have to be handled.
      const id = (v: unknown) =>
        typeof v === "string" ? v : ((v as { id?: string })?.id ?? "");
      return !lit.has(`${id(link.source)}\u0000${id(link.target)}`);
    };
  }, [trace]);

  if (loading && !graph) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-24 text-center text-muted-foreground">
        <p>Loading knowledge graph…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-24 text-center">
        <p className="text-destructive">Failed to load graph: {error}</p>
      </div>
    );
  }

  // Only an actually-empty graph takes over the page. A view emptied by a
  // filter has to keep the toolbar, or there is no way back out of it.
  if (!graph || !view || graph.nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-24 text-center text-muted-foreground">
        <h1 className="text-xl font-medium text-foreground">Graph Explorer</h1>
        <p>No entities yet — ingest a document to build the knowledge graph.</p>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-medium">Graph Explorer</h1>
        <Badge variant="outline">
          {view.nodes.length} entities · {view.edges.length} relationships
        </Badge>
        {view.is_truncated && (
          <Badge variant="secondary">truncated to top-degree nodes</Badge>
        )}
        {viewMode === "hop" && expanded.size > 0 && (
          <Badge variant="outline" className="tabular-nums">
            {expanded.size} expanded
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoading(true);
              setSelectedId(null);
              setViewMode((m) => (m === "hop" ? "full" : "hop"));
            }}
          >
            {viewMode === "hop" ? "Show full graph" : "Back to hop view"}
          </Button>
          {viewMode === "full" && sources.length > 0 && (
            <select
              value={focus}
              onChange={(e) => {
                // Reset here rather than in the effect: a synchronous setState
                // in an effect body is a cascading render (and a lint error).
                setLoading(true);
                setSelectedId(null);
                setFocus(e.target.value);
              }}
              aria-label="Focus the graph on one source"
              className="h-9 max-w-56 rounded-lg border border-input bg-background px-2 text-sm"
            >
              <option value="*">Whole graph</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.label}
                </option>
              ))}
              {focus !== "*" && !sources.some((s) => s.id === focus) && (
                <option value={focus}>{focus}</option>
              )}
            </select>
          )}
          <Input
            placeholder="Search entities…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-48"
          />
          <div className="flex overflow-hidden rounded-lg border border-input">
            <Button
              variant={mode === "2d" ? "secondary" : "ghost"}
              size="sm"
              className="rounded-none"
              onClick={() => setMode("2d")}
            >
              2D
            </Button>
            <Button
              variant={mode === "3d" ? "secondary" : "ghost"}
              size="sm"
              className="rounded-none"
              onClick={() => setMode("3d")}
            >
              3D
            </Button>
          </div>
          <Button
            variant={codeOnly ? "secondary" : "outline"}
            size="sm"
            aria-pressed={codeOnly}
            onClick={() => {
              setSelectedId(null);
              setCodeOnly((on) => !on);
            }}
          >
            Code only
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => graphRef.current?.zoomToFit?.(400, 40)}
          >
            Fit view
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <div
          ref={containerRef}
          className="relative min-w-0 flex-1 overflow-hidden rounded-xl border bg-muted/20"
        >
          <ForceGraphCanvas
            mode={mode}
            graphData={canvasData}
            width={size.width}
            height={size.height}
            selectedId={selectedId}
            colorFor={(t) => colorScale?.colorFor(t) ?? "#8b8a85"}
            hiddenFor={
              viewMode === "hop"
                ? (n) => hiddenCounts.get(n.id) ?? 0
                : undefined
            }
            dimmed={dimmed}
            dimmedLinks={dimmedLinks}
            settle={settle}
            onNodeClick={handleNodeClick}
            onBackgroundClick={() => setSelectedId(null)}
            graphRef={graphRef}
          />

          {/* Status for the hop itself: in flight, then whatever it found.
              Anchored over the canvas because that is where the action was. */}
          {(expanding || notice) && (
            <div
              role="status"
              aria-live="polite"
              className="pointer-events-none absolute inset-x-0 top-3 flex justify-center px-3"
            >
              <div
                className={`pointer-events-auto flex max-w-full items-center gap-2 rounded-full border px-3 py-1.5 text-xs shadow-sm backdrop-blur transition-opacity duration-200 ${
                  notice?.tone === "error"
                    ? "border-destructive/40 bg-destructive/10 text-destructive"
                    : "border-border bg-background/90 text-muted-foreground"
                }`}
              >
                {expanding ? (
                  <>
                    <Loader2 aria-hidden className="size-3.5 shrink-0 animate-spin" />
                    <span className="truncate">Expanding {label(expanding)}…</span>
                  </>
                ) : (
                  <>
                    <span className="truncate">{notice?.text}</span>
                    <button
                      onClick={() => setNotice(null)}
                      className="shrink-0 rounded-full p-0.5 hover:bg-foreground/10"
                      aria-label="Dismiss"
                    >
                      <X aria-hidden className="size-3" />
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Teaches the gesture once, over the nodes it applies to, and
              retires as soon as the first expansion lands. */}
          {viewMode === "hop" && expanded.size === 0 && !expanding && (
            <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
              <p className="rounded-full bg-background/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur">
                {view.nodes.length} source{view.nodes.length === 1 ? "" : "s"} —
                double-click one to open it
              </p>
            </div>
          )}

          {trace && (
            <div className="absolute top-3 right-3 flex items-center gap-2 rounded-lg bg-background/85 px-3 py-1.5 text-xs backdrop-blur">
              <span className="text-muted-foreground">
                Tracing {trace.incoming.length} caller(s) ·{" "}
                {trace.out.length} callee(s)
              </span>
              <Button variant="ghost" size="sm" onClick={() => setSelectedId(null)}>
                Clear focus
              </Button>
            </div>
          )}

          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 backdrop-blur-[1px]">
              <Loader2 aria-hidden className="size-5 animate-spin text-muted-foreground" />
            </div>
          )}

          {view.nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-muted-foreground">
              Nothing in the graph for {focus} yet — re-upload it if it was
              ingested before this view existed.
            </div>
          )}

          {colorScale && (
            <div className="absolute bottom-3 left-3 flex max-w-[70%] flex-wrap gap-x-3 gap-y-1 rounded-lg bg-background/80 px-3 py-2 text-xs backdrop-blur">
              {colorScale.legend.map((entry) => {
                const symbol = symbolFor(entry.type);
                const Icon = symbol?.icon;
                return (
                  <span key={entry.type} className="flex items-center gap-1.5">
                    {Icon ? (
                      <Icon
                        aria-hidden
                        className="size-3.5 shrink-0"
                        style={{ color: entry.color }}
                      />
                    ) : (
                      <span
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: entry.color }}
                      />
                    )}
                    <span className="capitalize text-muted-foreground">
                      {symbol?.label ?? entry.type}
                    </span>
                  </span>
                );
              })}
            </div>
          )}
        </div>

        <Card className="w-72 shrink-0 overflow-y-auto">
          <CardContent>
            {!selectedNode ? (
              <p className="text-sm text-muted-foreground">
                Click a node to see its details. Drag to pan, scroll to zoom.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <div>
                  <p className="flex items-start gap-1.5 font-medium">
                    {(() => {
                      const Icon = symbolFor(
                        selectedNode.entity_type,
                        selectedNode.source_type
                      )?.icon;
                      return Icon ? (
                        <Icon
                          aria-hidden
                          className="mt-0.5 size-4 shrink-0"
                          style={{
                            color: colorScale?.colorFor(selectedNode.entity_type),
                          }}
                        />
                      ) : null;
                    })()}
                    <span className="min-w-0 break-words">{selectedNode.id}</span>
                  </p>
                  {selectedNode.entity_type && (
                    <Badge variant="outline" className="mt-1 capitalize">
                      {selectedNode.entity_type}
                    </Badge>
                  )}
                </div>
                {selectedNode.description && (
                  <p className="text-sm text-muted-foreground">
                    {selectedNode.description}
                  </p>
                )}
                {selectedNode.file_path && (
                  <p className="text-xs text-muted-foreground">
                    Source: {selectedNode.file_path}
                  </p>
                )}
                {(() => {
                  const hiddenHere = hiddenCounts.get(selectedNode.id) ?? 0;
                  const busy = expanding === selectedNode.id;
                  return (
                    <Button
                      variant={hiddenHere > 0 ? "outline" : "ghost"}
                      size="sm"
                      disabled={busy || (hiddenHere === 0 && viewMode === "hop")}
                      onClick={() => void hop(selectedNode.id)}
                    >
                      {busy ? (
                        <Loader2 aria-hidden className="size-3.5 animate-spin" />
                      ) : null}
                      {busy
                        ? "Expanding…"
                        : hiddenHere > 0
                          ? `Expand ${hiddenHere} more`
                          : viewMode === "hop"
                            ? "Fully expanded"
                            : "Expand one hop"}
                    </Button>
                  );
                })()}
                {viewMode === "full" && focus !== selectedNode.id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setLoading(true);
                      setFocus(selectedNode.id);
                    }}
                  >
                    Center the full graph here
                  </Button>
                )}
                {viewMode === "full" && focus !== "*" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setLoading(true);
                      setSelectedId(null);
                      setFocus("*");
                    }}
                  >
                    Back to whole graph
                  </Button>
                )}
                {selectedNode.qualified_name && (
                  <p className="font-mono text-xs break-all text-muted-foreground">
                    {selectedNode.signature || selectedNode.qualified_name}
                  </p>
                )}
                {trace && (
                  <div className="flex flex-col gap-3">
                    {(
                      [
                        // Counts in the headers are the stored project-wide
                        // totals; the lists are what is in the loaded
                        // subgraph, which can be smaller when it is truncated.
                        {
                          title: "Calls",
                          total: selectedNode.calls_out_count,
                          edges: trace.out,
                          other: (e: GraphEdge) => e.target,
                        },
                        {
                          title: "Called by",
                          total: selectedNode.calls_in_count,
                          edges: trace.incoming,
                          other: (e: GraphEdge) => e.source,
                        },
                      ] as const
                    ).map((group) =>
                      group.edges.length === 0 ? null : (
                        <div key={group.title}>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">
                            {group.title} ({group.total ?? group.edges.length})
                          </p>
                          <ul className="flex flex-col gap-1">
                            {group.edges.map((e) => {
                              const other = group.other(e);
                              return (
                                <li key={e.id} className="text-xs">
                                  <button
                                    className="text-left font-medium break-all text-foreground hover:underline"
                                    onClick={() => setSelectedId(other)}
                                  >
                                    {other.split("::").pop()}
                                  </button>
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      )
                    )}
                  </div>
                )}
                {selectedEdges.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">
                      Relationships ({selectedEdges.length})
                    </p>
                    <ul className="flex flex-col gap-2">
                      {selectedEdges.map((e) => {
                        const other =
                          e.source === selectedNode.id ? e.target : e.source;
                        return (
                          <li key={e.id} className="text-xs">
                            <button
                              className="font-medium text-foreground hover:underline"
                              onClick={() => setSelectedId(other)}
                            >
                              {other}
                            </button>
                            {e.keywords && (
                              <span className="text-muted-foreground">
                                {" "}
                                — {e.keywords}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
