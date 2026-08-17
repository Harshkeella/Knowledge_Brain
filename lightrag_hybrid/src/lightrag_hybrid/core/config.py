"""Configuration management for LightRAG Hybrid."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Settings
    groq_api_key: Optional[str] = Field(default=None, alias="GROQ_API_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    default_llm_model: str = Field(default="llama-3.3-70b", alias="DEFAULT_LLM_MODEL")
    default_embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", alias="DEFAULT_EMBEDDING_MODEL")

    # Chunking
    chunk_size: int = Field(default=512, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=64, alias="CHUNK_OVERLAP")

    # Extraction
    extraction_workers: int = Field(default=12, alias="EXTRACTION_WORKERS")
    extraction_max_retries: int = 3
    extraction_timeout: float = 60.0

    # Vector DB
    vector_db_path: str = Field(default="./data/vector_db", alias="VECTOR_DB_PATH")
    vector_collection_name: str = "lightrag_chunks"
    top_k_vector: int = Field(default=10, alias="TOP_K_VECTOR")

    # Graph DB
    graph_db_path: str = Field(default="./data/graph_db", alias="GRAPH_DB_PATH")
    top_k_graph: int = Field(default=15, alias="TOP_K_GRAPH")
    max_hops: int = Field(default=2, alias="MAX_HOPS")

    # Retrieval
    rrf_k: int = Field(default=60, alias="RRF_K")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def vector_db_full_path(self) -> Path:
        return Path(self.vector_db_path).resolve()

    @property
    def graph_db_full_path(self) -> Path:
        return Path(self.graph_db_path).resolve()


@lru_cache()
def get_settings() -> Settings:
    return Settings()
