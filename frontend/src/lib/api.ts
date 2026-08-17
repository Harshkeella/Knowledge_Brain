const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SourceType =
  | "pdf"
  | "markdown"
  | "text"
  | "article"
  | "article_zenrows"
  | "youtube"
  | "paste";

export interface KnowledgeDocument {
  doc_id: string;
  file_name: string;
  source_type: SourceType;
  chunk_count: number;
  size_bytes: number;
  date_added: string;
}

export interface IngestResult extends KnowledgeDocument {
  deduped: boolean;
}

export interface IngestFileResponse {
  results: IngestResult[];
  errors: { file_name: string; error: string }[];
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.detail ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function listKnowledgeBase(): Promise<KnowledgeDocument[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/knowledge-base`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function deleteKnowledgeDocument(docId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/knowledge-base/${encodeURIComponent(docId)}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(await parseErrorDetail(res));
}

export async function ingestFiles(files: File[]): Promise<IngestFileResponse> {
  const formData = new FormData();
  for (const file of files) formData.append("files", file);

  const res = await fetch(`${API_BASE_URL}/api/v1/ingest/file`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function ingestUrl(url: string): Promise<IngestResult> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ingest/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function ingestText(
  text: string,
  title?: string
): Promise<IngestResult> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ingest/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, title }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export interface ChatSource {
  reference_id: string;
  file_path: string;
}

export interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatStreamHandlers {
  onSources?: (sources: ChatSource[]) => void;
  onToken?: (text: string) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export async function streamChat(
  message: string,
  history: ChatHistoryMessage[],
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
    signal,
  });
  if (!res.ok || !res.body) {
    handlers.onError?.(await parseErrorDetail(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5).trim());

      if (event.type === "sources") handlers.onSources?.(event.sources);
      else if (event.type === "token") handlers.onToken?.(event.text);
      else if (event.type === "error") handlers.onError?.(event.message);
      else if (event.type === "done") handlers.onDone?.();
    }
  }
}

export interface GraphNode {
  id: string;
  entity_type: string | null;
  description: string | null;
  file_path: string | null;
  degree: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  keywords: string | null;
  description: string | null;
  weight: number | null;
  file_path: string | null;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  is_truncated: boolean;
}

export async function getGraph(params?: {
  label?: string;
  maxDepth?: number;
  maxNodes?: number;
}): Promise<Graph> {
  const search = new URLSearchParams();
  if (params?.label) search.set("label", params.label);
  if (params?.maxDepth) search.set("max_depth", String(params.maxDepth));
  if (params?.maxNodes) search.set("max_nodes", String(params.maxNodes));

  const res = await fetch(`${API_BASE_URL}/api/v1/graph?${search.toString()}`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}
