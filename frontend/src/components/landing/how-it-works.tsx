"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";

/** Flips `data-visible` on once the element has scrolled into view. */
function useReveal() {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => entry.isIntersecting && setVisible(true),
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return { ref, visible };
}

const stagger = (i: number) => ({ "--i": i }) as CSSProperties;

function Beat({
  step,
  title,
  body,
  children,
}: {
  step: string;
  title: string;
  body: string;
  children: React.ReactNode;
}) {
  const { ref, visible } = useReveal();

  return (
    <div className="grid items-center gap-10 md:grid-cols-2 md:gap-16">
      <div>
        <span className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
          {step}
        </span>
        <h3 className="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">
          {title}
        </h3>
        <p className="mt-4 max-w-md text-pretty text-muted-foreground">
          {body}
        </p>
      </div>
      <div
        ref={ref}
        data-visible={visible}
        className="beat-figure rounded-xl border bg-card/40 p-4"
      >
        {children}
      </div>
    </div>
  );
}

/* Beat 1 — a source document splitting into overlapping chunks. */
function ChunkingFigure() {
  return (
    <svg viewBox="0 0 340 190" className="w-full" aria-hidden="true">
      <rect
        data-part="doc"
        x="16"
        y="42"
        width="78"
        height="106"
        rx="8"
        fill="var(--graph-series-1)"
        fillOpacity="0.12"
        stroke="var(--graph-series-1)"
        strokeWidth="1.5"
      />
      {[62, 76, 90, 104, 118].map((y, i) => (
        <rect
          key={y}
          data-part="doc"
          x="30"
          y={y}
          width={i === 4 ? 30 : 50}
          height="5"
          rx="2.5"
          fill="var(--graph-series-1)"
          fillOpacity="0.5"
        />
      ))}

      <path
        data-part="doc"
        d="M108 95h26m0 0-6-6m6 6-6 6"
        stroke="var(--graph-other)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />

      {[0, 1, 2, 3].map((i) => (
        <g key={i} data-part="chunk" style={stagger(i + 1)}>
          <rect
            x={158 + i * 44}
            y={52 + (i % 2) * 18}
            width="38"
            height="68"
            rx="6"
            fill="var(--graph-series-3)"
            fillOpacity="0.14"
            stroke="var(--graph-series-3)"
            strokeWidth="1.5"
          />
          <rect
            x={166 + i * 44}
            y={66 + (i % 2) * 18}
            width="22"
            height="4"
            rx="2"
            fill="var(--graph-series-3)"
            fillOpacity="0.6"
          />
          <rect
            x={166 + i * 44}
            y={76 + (i % 2) * 18}
            width="16"
            height="4"
            rx="2"
            fill="var(--graph-series-3)"
            fillOpacity="0.6"
          />
        </g>
      ))}

      <text
        data-part="chunk"
        style={stagger(5)}
        x="228"
        y="150"
        textAnchor="middle"
        className="fill-muted-foreground text-[10px]"
      >
        512 tokens · ~10% overlap
      </text>
    </svg>
  );
}

/* Beat 2 — chunks resolving into a labelled entity graph. Node colours and
   degree-based sizing mirror components/graph/entity-colors.ts. */
const NODES = [
  { x: 58, y: 62, r: 9, c: 3, label: "" },
  { x: 132, y: 38, r: 13, c: 2, label: "Acme Corp" },
  { x: 112, y: 122, r: 8, c: 3, label: "" },
  { x: 206, y: 88, r: 17, c: 1, label: "Q3 Report" },
  { x: 282, y: 44, r: 7, c: 4, label: "" },
  { x: 272, y: 140, r: 11, c: 5, label: "Invoice" },
  { x: 176, y: 156, r: 7, c: 3, label: "" },
];
const EDGES: [number, number][] = [
  [0, 1],
  [1, 2],
  [1, 3],
  [2, 3],
  [3, 4],
  [3, 5],
  [5, 6],
  [2, 6],
];

function GraphFigure() {
  return (
    <svg viewBox="0 0 340 190" className="w-full" aria-hidden="true">
      {EDGES.map(([a, b], i) => (
        <line
          key={i}
          data-part="edge"
          style={stagger(i)}
          x1={NODES[a].x}
          y1={NODES[a].y}
          x2={NODES[b].x}
          y2={NODES[b].y}
          stroke="var(--graph-link)"
          strokeWidth="1.5"
        />
      ))}
      {NODES.map((n, i) => (
        <g key={i} data-part="node" style={stagger(i + 2)}>
          <circle
            cx={n.x}
            cy={n.y}
            r={n.r}
            fill={`var(--graph-series-${n.c})`}
          />
          {n.label && (
            <text
              x={n.x}
              y={n.y + n.r + 12}
              textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {n.label}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

/* Beat 3 — the vector scatter and the graph converge, then an answer. */
const SCATTER = [
  [104, 52],
  [138, 74],
  [96, 96],
  [150, 118],
  [118, 140],
  [166, 44],
  [82, 70],
  [130, 158],
];

function RetrievalFigure() {
  return (
    <>
      <svg viewBox="0 0 340 190" className="w-full" aria-hidden="true">
        <g data-part="scatter">
          {SCATTER.map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r="3.5"
              fill="var(--graph-series-4)"
              fillOpacity="0.75"
            />
          ))}
        </g>
        <g data-part="graph">
          {EDGES.slice(0, 5).map(([a, b], i) => (
            <line
              key={i}
              x1={NODES[a].x}
              y1={NODES[a].y}
              x2={NODES[b].x}
              y2={NODES[b].y}
              stroke="var(--graph-link)"
              strokeWidth="1.5"
            />
          ))}
          {NODES.slice(0, 5).map((n, i) => (
            <circle
              key={i}
              cx={n.x}
              cy={n.y}
              r={n.r * 0.8}
              fill={`var(--graph-series-${n.c})`}
            />
          ))}
        </g>
      </svg>

      <div
        data-part="answer"
        className="mt-2 space-y-2 rounded-lg border bg-background/60 p-3 text-sm"
      >
        <p className="text-muted-foreground">
          &ldquo;Which invoices did Acme send us in Q3?&rdquo;
        </p>
        <p className="font-medium">
          Three, totalling $48,200 &mdash;{" "}
          <span className="text-muted-foreground font-normal">
            2 sources cited
          </span>
        </p>
      </div>
    </>
  );
}

export function HowItWorks() {
  return (
    <section
      id="how-its-stored"
      className="scroll-mt-8 bg-background px-6 py-24 text-foreground sm:py-32"
    >
      <div className="mx-auto max-w-5xl">
        <h2 className="max-w-2xl text-3xl font-semibold tracking-tight text-balance sm:text-4xl">
          How your data is stored
        </h2>
        <p className="mt-4 max-w-2xl text-muted-foreground">
          Everything below happens on your own machine. No document is shipped
          off to be indexed.
        </p>

        <div className="mt-20 space-y-24">
          <Beat
            step="Step 01 — Ingest"
            title="Drop it in, whatever it is"
            body="PDFs, web articles, YouTube links, spreadsheets, pasted notes. Each source is parsed to plain text, de-duplicated by hash so the same file never lands twice, then split into 512-token chunks with a little overlap and embedded locally."
          >
            <ChunkingFigure />
          </Beat>

          <Beat
            step="Step 02 — Extract"
            title="It finds what matters"
            body="A local model reads every ~150-word window and pulls out the people, companies, and things it mentions, along with how they relate. No per-chunk API call, so there is no rate limit to wait on. The result is a graph you can open and click through."
          >
            <GraphFigure />
          </Beat>

          <Beat
            step="Step 03 — Query"
            title="Ask, don't search"
            body="A question searches the embeddings and the graph at the same time. A re-ranker throws out the passages that only looked relevant, and the answer streams back with every source it leaned on listed underneath."
          >
            <RetrievalFigure />
          </Beat>
        </div>
      </div>
    </section>
  );
}
