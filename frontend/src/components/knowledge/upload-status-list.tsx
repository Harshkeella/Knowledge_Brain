"use client";

import { Badge } from "@/components/ui/badge";
import { useKnowledgeStore } from "@/store/knowledge-store";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

// Ingest is a single request with no server-side progress events, so these are
// timed stages, not measured ones — deliberately no percentage bar, and the
// last stage holds until the request actually returns.
// ponytail: swap the timer for real SSE stages if ingest ever streams progress.
const STAGES = [
  "Scraping the page...",
  "Cleaning the article...",
  "Chunking + embedding...",
  "Building the knowledge graph...",
] as const;
const STAGE_MS = 4000;

function useStage(active: boolean) {
  const [stage, setStage] = useState(0);

  useEffect(() => {
    if (!active) return;
    setStage(0);
    const id = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      STAGE_MS
    );
    return () => clearInterval(id);
  }, [active]);

  return STAGES[stage];
}

function UploadRow({
  label,
  status,
  error,
}: {
  label: string;
  status: string;
  error?: string;
}) {
  const stage = useStage(status === "processing");

  return (
    <div className="relative flex items-center justify-between gap-3 overflow-hidden rounded-md border px-3 py-2 text-sm">
      {/* Indeterminate sweep along the row while work is in flight. */}
      {status === "processing" && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 h-full w-1/3 animate-[sweep_1.8s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-foreground/5 to-transparent"
        />
      )}
      <span className="truncate">{label}</span>
      <Badge
        variant={
          status === "error"
            ? "destructive"
            : status === "done"
              ? "default"
              : "secondary"
        }
        className="shrink-0 gap-1"
      >
        {status === "processing" && <Loader2 className="size-3 animate-spin" />}
        {status === "done" && <CheckCircle2 className="size-3" />}
        {status === "error" && <XCircle className="size-3" />}
        {status === "error"
          ? error || "Failed"
          : status === "processing"
            ? stage
            : status === "done"
              ? "Indexed successfully"
              : "Queued..."}
      </Badge>
    </div>
  );
}

export function UploadStatusList() {
  const uploads = useKnowledgeStore((s) => s.uploads);

  if (uploads.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {uploads.map((u) => (
        <UploadRow
          key={u.id}
          label={u.label}
          status={u.status}
          error={u.error}
        />
      ))}
    </div>
  );
}
