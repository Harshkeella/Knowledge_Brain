from pydantic import BaseModel


class DocumentOut(BaseModel):
    doc_id: str
    file_name: str
    source_type: str
    chunk_count: int
    size_bytes: int
    date_added: str


class IngestResult(BaseModel):
    doc_id: str
    file_name: str
    source_type: str
    chunk_count: int
    size_bytes: int
    date_added: str
    deduped: bool


class UrlIngestRequest(BaseModel):
    url: str


class TextIngestRequest(BaseModel):
    text: str
    title: str | None = None
    # "article_clipper" for the extension's client-side extraction; anything
    # else (or nothing) is a plain paste.
    source_type: str | None = None


class FolderIngestRequest(BaseModel):
    path: str
    name: str | None = None
    # Document leaves (pdf/md/txt/xlsx) go through the normal ingestors so
    # their contents are actually searchable. Off makes this a pure structure
    # scan, which is seconds instead of minutes on a repo full of PDFs.
    index_documents: bool = True


class FolderIngestResult(BaseModel):
    doc_id: str
    name: str
    path: str
    date_added: str
    size_bytes: int
    folders: int
    files: int
    code_files: int
    images: int
    videos: int
    # Documents are routed through the ordinary ingestors AFTER this response
    # is sent (see folder_ingest._index_documents), so this is 0 on the way out
    # and `documents_pending` is the queue. `status` and the final count live
    # on the Source node.
    documents_indexed: int
    documents_pending: int = 0
    status: str = "completed"
    max_depth_reached: int = 0
    total_folders: int = 0
    total_files: int = 0
    classes: int
    functions: int
    methods: int
    calls: int
    # Call targets outside the scanned tree that were traceable to an import,
    # so they became real (deduped) nodes instead of a lost string.
    external_symbols: int
    # Calls whose target could not be placed at all -- builtins and methods on
    # locals. Kept on the caller node rather than wired to a guess.
    unresolved: int
    errors: list[dict]


class DeleteResult(BaseModel):
    doc_id: str
    deleted: bool


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    # When set, the turn is persisted to that session and the session's title
    # is generated on the first exchange. Omitted, chat behaves exactly as it
    # did before sessions existed -- nothing is written.
    session_id: str | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class SessionRenameRequest(BaseModel):
    title: str


class SessionMessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: str
    # Per-source provenance for an assistant turn: what the answer was built
    # from. Persisted so reopening an old session still opens working panels.
    evidence: list[dict] = []


class GraphNodeOut(BaseModel):
    id: str
    entity_type: str | None = None
    description: str | None = None
    file_path: str | None = None
    degree: int
    # Only Source nodes carry this: which kind of upload they came from, so the
    # UI can tell a circled globe from a circled folder. Additive and optional
    # -- every other node leaves it null and nothing existing reads it.
    source_type: str | None = None
    # Code symbols only. The counts are the TRUE project-wide totals, computed
    # at write time -- the loaded subgraph is usually truncated, so counting
    # its edges would under-report "who calls this".
    qualified_name: str | None = None
    signature: str | None = None
    calls_in_count: int | None = None
    calls_out_count: int | None = None
    # Source nodes only: processing | completed | failed.
    status: str | None = None


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    keywords: str | None = None
    # structural | behavioral | semantic -- derived from `keywords`, so every
    # edge has one, including the ones LightRAG wrote before this existed.
    edge_category: str = "semantic"
    description: str | None = None
    weight: float | None = None
    file_path: str | None = None


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    is_truncated: bool
