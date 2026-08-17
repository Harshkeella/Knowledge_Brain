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


class DeleteResult(BaseModel):
    doc_id: str
    deleted: bool


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class GraphNodeOut(BaseModel):
    id: str
    entity_type: str | None = None
    description: str | None = None
    file_path: str | None = None
    degree: int


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    keywords: str | None = None
    description: str | None = None
    weight: float | None = None
    file_path: str | None = None


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    is_truncated: bool
