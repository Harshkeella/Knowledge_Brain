"use client";

import { AlertTriangle, Check, Copy, Route } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type {
  ChatSource,
  EvidenceSource,
  GroundingVerdict,
  TableResult,
} from "@/lib/api";
import { DataResultTable } from "@/components/chat/data-result-table";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  evidence?: EvidenceSource[];
  grounding?: GroundingVerdict;
  table?: TableResult;
  error?: string;
  streaming?: boolean;
}

function CodeBlock({ children, ...props }: React.ComponentProps<"pre">) {
  const [copied, setCopied] = useState(false);

  async function copy(e: React.MouseEvent<HTMLButtonElement>) {
    // The rendered text of the block, straight off the DOM -- reconstructing
    // it from React children means walking the highlighter's element tree.
    const code = e.currentTarget.parentElement?.querySelector("code")?.textContent;
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard is permission-gated and absent over plain http; the button
      // simply does nothing rather than throwing into the render tree.
    }
  }

  return (
    <div className="group/code relative my-3">
      <button
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy code"}
        className="absolute top-2 right-2 rounded border bg-background/90 p-1.5 opacity-0 transition-opacity group-hover/code:opacity-100 focus-visible:opacity-100"
      >
        {copied ? (
          <Check className="size-3.5 text-green-600" />
        ) : (
          <Copy className="size-3.5" />
        )}
      </button>
      <pre
        className="overflow-x-auto rounded-lg border bg-muted/50 p-3 text-xs leading-relaxed"
        {...props}
      >
        {children}
      </pre>
    </div>
  );
}

const markdownComponents = {
  p: ({ ...props }) => <p className="mb-3 leading-7 last:mb-0" {...props} />,
  ul: ({ ...props }) => (
    <ul className="mb-3 list-disc space-y-1 pl-5 leading-7 last:mb-0" {...props} />
  ),
  ol: ({ ...props }) => (
    <ol className="mb-3 list-decimal space-y-1 pl-5 leading-7 last:mb-0" {...props} />
  ),
  h1: ({ ...props }) => (
    <h1 className="mt-5 mb-2 text-lg font-semibold first:mt-0" {...props} />
  ),
  h2: ({ ...props }) => (
    <h2 className="mt-5 mb-2 text-base font-semibold first:mt-0" {...props} />
  ),
  h3: ({ ...props }) => (
    <h3 className="mt-4 mb-1 text-sm font-semibold first:mt-0" {...props} />
  ),
  a: ({ ...props }) => (
    <a
      className="underline underline-offset-2 hover:text-primary"
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    />
  ),
  code: ({ className, ...props }: React.ComponentProps<"code">) =>
    // Inline code only: a fenced block's <code> arrives with a language class
    // from the highlighter and must keep its own styling.
    className?.includes("hljs") || className?.includes("language-") ? (
      <code className={className} {...props} />
    ) : (
      <code
        className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]"
        {...props}
      />
    ),
  pre: CodeBlock,
  blockquote: ({ ...props }) => (
    <blockquote
      className="mb-3 border-l-2 pl-3 text-muted-foreground italic last:mb-0"
      {...props}
    />
  ),
  table: ({ ...props }) => (
    <div className="mb-3 overflow-x-auto last:mb-0">
      <table className="w-full text-sm" {...props} />
    </div>
  ),
  th: ({ ...props }) => (
    <th className="border px-2 py-1 text-left font-semibold" {...props} />
  ),
  td: ({ ...props }) => <td className="border px-2 py-1" {...props} />,
};

function Sources({
  sources,
  evidence,
  onOpenEvidence,
}: {
  sources: ChatSource[];
  evidence?: EvidenceSource[];
  onOpenEvidence: (evidence: EvidenceSource) => void;
}) {
  return (
    <details className="mt-4 text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {sources.length} source{sources.length === 1 ? "" : "s"}
      </summary>
      <ul className="mt-2 space-y-1">
        {sources.map((source) => {
          const chain = evidence?.find(
            (e) => e.reference_id === source.reference_id
          );
          return (
            <li
              key={source.reference_id}
              className="flex items-center gap-1.5 rounded border px-2 py-1"
            >
              <span className="min-w-0 flex-1 truncate" title={source.file_path}>
                {source.file_path}
              </span>
              {/* Only rendered when there is a chain to show, so the button is
                  never a promise the panel can't keep. */}
              {chain && (
                <button
                  onClick={() => onOpenEvidence(chain)}
                  aria-label={`Show how ${source.file_path} was used`}
                  title="How this was used"
                  className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <Route className="size-3.5" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

export function ChatMessageBubble({
  message,
  onOpenEvidence,
}: {
  message: ChatMessage;
  onOpenEvidence: (evidence: EvidenceSource) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2.5 text-sm whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full text-sm">
      {message.table && (
        <div className="mb-3">
          <DataResultTable result={message.table} />
        </div>
      )}

      {message.content ? (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
          components={markdownComponents}
        >
          {message.content}
        </ReactMarkdown>
      ) : message.streaming && !message.error ? (
        <span className="text-muted-foreground">Thinking…</span>
      ) : null}

      {message.error && (
        <p className="mt-2 text-destructive">{message.error}</p>
      )}

      {/* Flagged, not hidden: the answer already streamed, so the honest move
          is to say which parts the evidence didn't cover. */}
      {message.grounding && message.grounding.unsupported.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2.5 text-xs">
          <p className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-500">
            <AlertTriangle className="size-3.5" />
            {message.grounding.unsupported.length} of {message.grounding.checked}{" "}
            statement
            {message.grounding.checked === 1 ? "" : "s"} not backed by the
            retrieved sources
          </p>
          <ul className="mt-1.5 space-y-1 text-muted-foreground">
            {message.grounding.unsupported.map((claim) => (
              <li key={claim} className="border-l-2 border-amber-500/40 pl-2">
                {claim}
              </li>
            ))}
          </ul>
        </div>
      )}

      {message.sources && message.sources.length > 0 && (
        <Sources
          sources={message.sources}
          evidence={message.evidence}
          onOpenEvidence={onOpenEvidence}
        />
      )}
    </div>
  );
}
