"""Pydantic models for the LightRAG Hybrid system."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    SPECIFIC_FACT = "SPECIFIC_FACT"
    GLOBAL_SUMMARY = "GLOBAL_SUMMARY"
    HYBRID_MULTI_HOP = "HYBRID_MULTI_HOP"


class DocumentSource(BaseModel):
    filename: str
    file_path: str
    file_type: str  # pdf, md, txt, etc.
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    id: str
    content: str
    source_doc: DocumentSource
    chunk_index: int
    token_count: int
    char_count: int
    content_hash: str  # SHA-256 hash for deduplication
    embedding: Optional[List[float]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Entity(BaseModel):
    name: str
    entity_type: str
    description: Optional[str] = None
    source_chunks: List[str] = Field(default_factory=list)  # chunk IDs
    source_docs: List[str] = Field(default_factory=list)  # filenames
    attributes: Dict[str, Any] = Field(default_factory=dict)
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)


class Relationship(BaseModel):
    source: str  # entity name
    target: str  # entity name
    relation_type: str
    description: Optional[str] = None
    weight: float = 1.0
    source_chunks: List[str] = Field(default_factory=list)
    source_docs: List[str] = Field(default_factory=list)
    bidirectional: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    chunk: Chunk
    vector_score: Optional[float] = None
    graph_score: Optional[float] = None
    rrf_score: Optional[float] = None
    matched_entities: List[str] = Field(default_factory=list)


class GraphPath(BaseModel):
    nodes: List[str]  # entity names in path
    edges: List[Relationship]
    path_length: int
    relevance_score: float = 1.0


class HybridContext(BaseModel):
    query: str
    intent: QueryIntent
    resolved_query: str  # after coreference resolution
    chunks: List[RetrievedChunk] = Field(default_factory=list)
    graph_paths: List[GraphPath] = Field(default_factory=list)
    top_entities: List[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: str  # user / assistant / system
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Citation(BaseModel):
    citation_type: str  # "chunk" or "graph"
    doc_name: Optional[str] = None
    chunk_id: Optional[str] = None
    snippet: Optional[str] = None
    source_entity: Optional[str] = None
    relation: Optional[str] = None
    target_entity: Optional[str] = None


class SourceReference(BaseModel):
    doc_name: str
    chunk_id: Optional[str] = None
    snippet: str
    graph_edge: Optional[str] = None


class RAGResponse(BaseModel):
    summary: str
    insights: List[str]
    graph_connections: List[str]
    citations: List[Citation]
    sources: List[SourceReference]
    confidence: float
    metadata: Dict[str, Any] = Field(default_factory=dict)
