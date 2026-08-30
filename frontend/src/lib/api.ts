const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
).replace(/\/+$/, "");

/**
 * How the client obtains a bearer token, installed by <AuthProvider>.
 *
 * A getter rather than a value: the auth SDK refreshes tokens in the
 * background, so a token captured once is the stale one by the time a long
 * chat stream reconnects. Left null when the app runs against a single-user
 * backend, where every request is unauthenticated by design.
 */
type AccessTokenSource = () => Promise<string | null>;

let accessTokenSource: AccessTokenSource | null = null;

export function setAccessTokenSource(source: AccessTokenSource | null): void {
  accessTokenSource = source;
}

/** Called on a 401 so the app can send the user back to sign in. */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

export class UnauthorizedError extends Error {
  constructor(message = "Your session has expired. Please sign in again.") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

/**
 * Every call to the API goes through here.
 *
 * Authentication is attached in one place rather than at each of the sixteen
 * call sites below, so a new endpoint cannot ship unauthenticated by
 * forgetting a header -- and no caller ever names a user id, because the
 * backend derives identity from this token and would ignore one anyway.
 */
async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = accessTokenSource ? await accessTokenSource() : null;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    onUnauthorized?.();
    throw new UnauthorizedError(await parseErrorDetail(res));
  }
  return res;
}

export type SourceType =
  | "pdf"
  | "markdown"
  | "text"
  | "article"
  | "article_zenrows"
  | "article_clipper"
  | "spreadsheet"
  | "youtube"
  | "paste"
  | "folder";

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
  const res = await apiFetch(`${API_BASE_URL}/api/v1/knowledge-base`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function deleteKnowledgeDocument(docId: string): Promise<void> {
  const res = await apiFetch(
    `${API_BASE_URL}/api/v1/knowledge-base/${encodeURIComponent(docId)}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(await parseErrorDetail(res));
}

export async function ingestFiles(files: File[]): Promise<IngestFileResponse> {
  const formData = new FormData();
  for (const file of files) formData.append("files", file);

  const res = await apiFetch(`${API_BASE_URL}/api/v1/ingest/file`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export interface FolderIngestResult extends KnowledgeDocument {
  name: string;
  path: string;
  folders: number;
  files: number;
  code_files: number;
  images: number;
  videos: number;
  documents_indexed: number;
  classes: number;
  functions: number;
  methods: number;
  calls: number;
  external_symbols: number;
  unresolved: number;
  /** Document leaves still going through the ingestors after this returned. */
  documents_pending: number;
  /** processing while those run, then completed | failed. */
  status: string;
  errors: { file_name: string; error: string }[];
}

export async function ingestFolder(params: {
  path: string;
  name?: string;
  indexDocuments?: boolean;
}): Promise<FolderIngestResult> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/ingest/folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path: params.path,
      name: params.name || null,
      index_documents: params.indexDocuments ?? true,
    }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function ingestUrl(url: string): Promise<IngestResult> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/ingest/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function undoComputedColumn(
  table: string,
  column: string
): Promise<void> {
  const res = await apiFetch(
    `${API_BASE_URL}/api/v1/knowledge-base/spreadsheet/${encodeURIComponent(
      table
    )}/columns/${encodeURIComponent(column)}`,
    { method: "DELETE" }
  );
  if (!res.ok) throw new Error(await parseErrorDetail(res));
}

export async function ingestText(
  text: string,
  title?: string
): Promise<IngestResult> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/ingest/text`, {
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

/** A spreadsheet answer: exact rows out of DuckDB, not LLM prose. */
export interface TableResult {
  columns: string[];
  rows: (string | number | boolean | null)[][];
  total_row_count: number;
  truncated: boolean;
  sql?: string;
  table?: string;
  added_column?: string;
}

/** One step of a provenance chain: what the answer was actually built from. */
export interface EvidenceStep {
  type: "source" | "chunk" | "entity" | "relationship";
  id: string;
  label: string;
  snippet: string;
  entity_type?: string;
  keywords?: string;
  src_id?: string;
  tgt_id?: string;
}

/** The chain for one source, keyed by the same reference_id as ChatSource. */
export interface EvidenceSource {
  reference_id: string;
  file_path: string;
  chain: EvidenceStep[];
}

/** Faithfulness verdict on the finished answer. Only sent when it found something. */
export interface GroundingVerdict {
  checked: number;
  unsupported: string[];
  supported_ratio: number;
}

export interface ChatStreamHandlers {
  onSources?: (sources: ChatSource[]) => void;
  onEvidence?: (evidence: EvidenceSource[]) => void;
  onGrounding?: (verdict: GroundingVerdict) => void;
  onTable?: (result: TableResult) => void;
  onToken?: (text: string) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export async function streamChat(
  message: string,
  history: ChatHistoryMessage[],
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
  sessionId?: string | null
): Promise<void> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, session_id: sessionId ?? null }),
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
      else if (event.type === "evidence") handlers.onEvidence?.(event.evidence);
      else if (event.type === "grounding") handlers.onGrounding?.(event.grounding);
      else if (event.type === "table") handlers.onTable?.(event.result);
      else if (event.type === "token") handlers.onToken?.(event.text);
      else if (event.type === "error") handlers.onError?.(event.message);
      else if (event.type === "done") handlers.onDone?.();
    }
  }
}

export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  evidence: EvidenceSource[];
}

const SESSIONS_URL = `${API_BASE_URL}/api/v1/chat/sessions`;

export async function createSession(): Promise<ChatSession> {
  const res = await apiFetch(SESSIONS_URL, { method: "POST" });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function listSessions(): Promise<ChatSession[]> {
  const res = await apiFetch(SESSIONS_URL);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function listSessionMessages(
  sessionId: string
): Promise<StoredMessage[]> {
  const res = await apiFetch(
    `${SESSIONS_URL}/${encodeURIComponent(sessionId)}/messages`
  );
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function renameSession(
  sessionId: string,
  title: string
): Promise<ChatSession> {
  const res = await apiFetch(`${SESSIONS_URL}/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`${SESSIONS_URL}/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
}

export interface GraphNode {
  id: string;
  entity_type: string | null;
  description: string | null;
  file_path: string | null;
  degree: number;
  /** Source nodes only: which kind of upload they represent. */
  source_type?: string | null;
  /** Source nodes only: processing | completed | failed. */
  status?: string | null;
  /** Code symbols only. Counts are project-wide totals, not subgraph counts. */
  qualified_name?: string | null;
  signature?: string | null;
  calls_in_count?: number | null;
  calls_out_count?: number | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  keywords: string | null;
  /** structural | behavioral | semantic -- what the edge means, for styling. */
  edge_category?: string;
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

  const res = await apiFetch(`${API_BASE_URL}/api/v1/graph?${search.toString()}`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

/**
 * Every Source node and nothing else -- the graph's landing state.
 *
 * The whole-graph load can never show a deep tree: it is a degree-capped BFS,
 * so a folder eight levels down is past the horizon of the only query asked.
 * Starting from the sources and hopping outwards is how you reach it.
 */
export async function getSources(): Promise<Graph> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/graph/sources`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

/** One hop out from a node: its immediate neighbours, both directions. */
export async function expandNode(nodeId: string): Promise<Graph> {
  const res = await apiFetch(
    `${API_BASE_URL}/api/v1/graph/expand?node_id=${encodeURIComponent(nodeId)}`
  );
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export interface StorageUsage {
  used_bytes: number;
  quota_bytes: number;
  remaining_bytes: number;
  document_count: number;
}

/**
 * What this user has stored. Informational only -- the backend enforces the
 * same numbers before it accepts an upload, so a stale reading here can never
 * let a byte past the quota.
 */
export async function getStorageUsage(): Promise<StorageUsage> {
  const res = await apiFetch(`${API_BASE_URL}/api/v1/me/usage`);
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}
