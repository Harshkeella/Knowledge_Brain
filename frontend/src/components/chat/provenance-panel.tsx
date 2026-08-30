"use client";

/**
 * The proof behind one source: a linear chain, deliberately not a graph.
 *
 * The hop-explorer's force layout is the wrong tool at this scale -- three to
 * a dozen nodes in a physics sim jitter into a shape that says nothing. A
 * chain reads in one direction and holds still, and the direction *is* the
 * claim: this document produced this text, which mentioned these entities,
 * which are connected this way, which is what the answer rests on.
 *
 * Icons and fixed colours come from `constants/symbols` -- the same table the
 * graph canvas and legend read, so an entity is the same colour here as it is
 * in the explorer. Every entity and relationship endpoint deep-links into the
 * full graph view via its existing `?focus=` parameter rather than
 * reimplementing exploration here.
 */

import { ArrowRight, FileText, Quote, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";
import type { EvidenceSource, EvidenceStep } from "@/lib/api";
import { symbolFor } from "@/constants/symbols";
import { cn } from "@/lib/utils";

function useIsDark() {
  // Read once per open: the panel is short-lived, and the graph's colour
  // helpers take the same boolean.
  return (
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
  );
}

function StepIcon({ step, dark }: { step: EvidenceStep; dark: boolean }) {
  if (step.type === "chunk") {
    return <Quote className="size-4 shrink-0 text-muted-foreground" />;
  }
  if (step.type === "relationship") {
    return <ArrowRight className="size-4 shrink-0 text-muted-foreground" />;
  }

  const spec = symbolFor(
    step.type === "source" ? "source" : step.entity_type,
    undefined
  );
  const Icon = spec?.icon ?? FileText;
  const color = spec ? (dark ? spec.dark : spec.light) : undefined;
  return <Icon className="size-4 shrink-0" style={color ? { color } : undefined} />;
}

/** Entities and relationship endpoints jump into the explorer; text does not. */
function StepLabel({ step }: { step: EvidenceStep }) {
  const graphHref = (id: string) =>
    `/dashboard/graph?focus=${encodeURIComponent(id)}`;

  if (step.type === "entity") {
    return (
      <Link
        href={graphHref(step.id)}
        className="font-medium underline-offset-2 hover:underline"
      >
        {step.label}
      </Link>
    );
  }

  if (step.type === "relationship" && step.src_id && step.tgt_id) {
    return (
      <span className="font-medium">
        <Link
          href={graphHref(step.src_id)}
          className="underline-offset-2 hover:underline"
        >
          {step.src_id}
        </Link>
        <span className="mx-1 text-muted-foreground">
          {step.keywords ? `—[${step.keywords}]→` : "→"}
        </span>
        <Link
          href={graphHref(step.tgt_id)}
          className="underline-offset-2 hover:underline"
        >
          {step.tgt_id}
        </Link>
      </span>
    );
  }

  return <span className="font-medium break-all">{step.label}</span>;
}

const KIND_LABEL: Record<EvidenceStep["type"], string> = {
  source: "Source document",
  chunk: "Matched text",
  entity: "Entity",
  relationship: "Relationship",
};

export function ProvenancePanel({
  evidence,
  onClose,
}: {
  evidence: EvidenceSource;
  onClose: () => void;
}) {
  const dark = useIsDark();
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape closes, and focus moves into the panel so a keyboard user is not
  // stranded behind it.
  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const steps = [
    ...evidence.chain,
    // The terminal step: what all of it was for.
    { type: "answer" as const, id: "answer", label: "Answer", snippet: "" },
  ];

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/20 md:bg-transparent"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-label={`Evidence for ${evidence.file_path}`}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl outline-none"
      >
        <header className="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">How this was answered</h2>
            <p className="truncate text-xs text-muted-foreground" title={evidence.file_path}>
              {evidence.file_path}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close evidence panel"
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </header>

        <ol className="flex-1 overflow-y-auto px-4 py-4">
          {steps.map((step, i) => {
            const last = i === steps.length - 1;
            return (
              <li key={`${step.type}-${step.id}-${i}`} className="relative flex gap-3">
                {/* The spine: a continuous rule behind the markers, so the
                    chain reads as one path rather than stacked cards. */}
                <div className="flex flex-col items-center">
                  <div className="flex size-7 items-center justify-center rounded-full border bg-background">
                    {step.type === "answer" ? (
                      <span className="text-[10px] font-semibold">A</span>
                    ) : (
                      <StepIcon step={step as EvidenceStep} dark={dark} />
                    )}
                  </div>
                  {!last && <div className="w-px flex-1 bg-border" aria-hidden="true" />}
                </div>

                <div className={cn("min-w-0 flex-1", last ? "pb-0" : "pb-5")}>
                  <p className="text-[10px] tracking-wide text-muted-foreground uppercase">
                    {step.type === "answer"
                      ? "Answer"
                      : KIND_LABEL[step.type as EvidenceStep["type"]]}
                  </p>
                  <div className="mt-0.5 text-sm">
                    {step.type === "answer" ? (
                      <span className="font-medium text-muted-foreground">
                        Grounded in the {evidence.chain.length - 1} item
                        {evidence.chain.length - 1 === 1 ? "" : "s"} above
                      </span>
                    ) : (
                      <StepLabel step={step as EvidenceStep} />
                    )}
                  </div>
                  {step.snippet && (
                    <p className="mt-1 border-l-2 pl-2 text-xs leading-relaxed text-muted-foreground">
                      {step.snippet}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </aside>
    </>
  );
}
