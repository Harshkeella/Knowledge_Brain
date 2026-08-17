"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { getGraph, type Graph } from "@/lib/api";
import { buildEntityColorScale } from "./entity-colors";
import { ForceGraphCanvas, type ForceGraphRef } from "./force-graph-canvas";

export function GraphExplorer() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"2d" | "3d">("2d");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphRef | undefined>(undefined);
  const [size, setSize] = useState({ width: 800, height: 600 });

  useEffect(() => {
    let cancelled = false;
    getGraph({ maxNodes: 300 })
      .then((g) => {
        if (!cancelled) setGraph(g);
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
  }, []);

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

  const colorScale = useMemo(
    () => (graph ? buildEntityColorScale(graph.nodes) : null),
    [graph]
  );

  const canvasData = useMemo(() => {
    if (!graph || !colorScale) return { nodes: [], links: [] };
    return {
      nodes: graph.nodes.map((n) => ({
        id: n.id,
        entity_type: n.entity_type,
        description: n.description,
        file_path: n.file_path,
        degree: n.degree,
        color: colorScale.colorFor(n.entity_type),
      })),
      links: graph.edges.map((e) => ({
        source: e.source,
        target: e.target,
        keywords: e.keywords,
        description: e.description,
        weight: e.weight,
      })),
    };
  }, [graph, colorScale]);

  const dimmed = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q || !graph) return null;
    const matched = new Set(
      graph.nodes.filter((n) => n.id.toLowerCase().includes(q)).map((n) => n.id)
    );
    const keep = new Set(matched);
    for (const e of graph.edges) {
      if (matched.has(e.source)) keep.add(e.target);
      if (matched.has(e.target)) keep.add(e.source);
    }
    const dim = new Set<string>();
    for (const n of graph.nodes) if (!keep.has(n.id)) dim.add(n.id);
    return dim;
  }, [search, graph]);

  const selectedNode = useMemo(
    () => graph?.nodes.find((n) => n.id === selectedId) ?? null,
    [graph, selectedId]
  );

  const selectedEdges = useMemo(() => {
    if (!graph || !selectedId) return [];
    return graph.edges.filter(
      (e) => e.source === selectedId || e.target === selectedId
    );
  }, [graph, selectedId]);

  if (loading) {
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

  if (!graph || graph.nodes.length === 0) {
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
          {graph.nodes.length} entities · {graph.edges.length} relationships
        </Badge>
        {graph.is_truncated && (
          <Badge variant="secondary">truncated to top-degree nodes</Badge>
        )}
        <div className="ml-auto flex items-center gap-2">
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
            dimmed={dimmed}
            onNodeClick={(id) => setSelectedId(id)}
            onBackgroundClick={() => setSelectedId(null)}
            graphRef={graphRef}
          />

          {colorScale && (
            <div className="absolute bottom-3 left-3 flex max-w-[70%] flex-wrap gap-x-3 gap-y-1 rounded-lg bg-background/80 px-3 py-2 text-xs backdrop-blur">
              {colorScale.legend.map((entry) => (
                <span key={entry.type} className="flex items-center gap-1.5">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: entry.color }}
                  />
                  <span className="text-muted-foreground">{entry.type}</span>
                </span>
              ))}
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
                  <p className="font-medium">{selectedNode.id}</p>
                  {selectedNode.entity_type && (
                    <Badge variant="outline" className="mt-1">
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
