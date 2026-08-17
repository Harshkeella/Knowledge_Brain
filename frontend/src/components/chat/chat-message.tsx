import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ChatSource } from "@/lib/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  error?: string;
  streaming?: boolean;
}

const markdownComponents = {
  p: ({ ...props }) => <p className="mb-2 last:mb-0" {...props} />,
  ul: ({ ...props }) => (
    <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0" {...props} />
  ),
  ol: ({ ...props }) => (
    <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0" {...props} />
  ),
  li: ({ ...props }) => <li {...props} />,
  h1: ({ ...props }) => (
    <h1 className="mb-2 text-base font-semibold" {...props} />
  ),
  h2: ({ ...props }) => (
    <h2 className="mb-2 text-base font-semibold" {...props} />
  ),
  h3: ({ ...props }) => (
    <h3 className="mb-1 text-sm font-semibold" {...props} />
  ),
  a: ({ ...props }) => (
    <a
      className="underline underline-offset-2 hover:text-primary"
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    />
  ),
  code: ({ ...props }) => (
    <code
      className="rounded bg-background/60 px-1 py-0.5 font-mono text-xs"
      {...props}
    />
  ),
  pre: ({ ...props }) => (
    <pre
      className="mb-2 overflow-x-auto rounded bg-background/60 p-2 text-xs last:mb-0"
      {...props}
    />
  ),
  blockquote: ({ ...props }) => (
    <blockquote
      className="mb-2 border-l-2 pl-2 text-muted-foreground last:mb-0"
      {...props}
    />
  ),
  table: ({ ...props }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="text-xs" {...props} />
    </div>
  ),
  th: ({ ...props }) => (
    <th className="border px-2 py-1 text-left font-semibold" {...props} />
  ),
  td: ({ ...props }) => <td className="border px-2 py-1" {...props} />,
};

export function ChatMessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground whitespace-pre-wrap" : "bg-muted"
        )}
      >
        {!isUser && message.sources && message.sources.length > 0 && (
          <details className="mb-2 text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              {message.sources.length} source
              {message.sources.length === 1 ? "" : "s"}
            </summary>
            <div className="mt-1 flex flex-wrap gap-1">
              {message.sources.map((s) => (
                <Badge key={s.reference_id} variant="outline">
                  {s.file_path}
                </Badge>
              ))}
            </div>
          </details>
        )}

        {message.content ? (
          isUser ? (
            message.content
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          )
        ) : message.streaming && !message.error ? (
          <span className="text-muted-foreground">Thinking...</span>
        ) : null}

        {message.error && (
          <p className="mt-1 text-destructive">{message.error}</p>
        )}
      </div>
    </div>
  );
}
