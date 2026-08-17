import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    groq_api_key: str | None = os.getenv("GROQ_API_KEY") or None

    # Answering (chat). This is a TOKENS-PER-MINUTE decision, not a speed one:
    # a chat call sends the whole assembled retrieval context, so the model's
    # TPM ceiling has to exceed QUERY_CONTEXT_TOKEN_BUDGET with room for the
    # answer. 8b-instant's 6,000 TPM could not fit LightRAG's default 30,000
    # token context, so every single chat call 429'd and silently fell back to
    # OpenRouter. 70b-versatile gets 12,000. It shares a bucket with
    # GROQ_EXTRACT_MODEL only when EXTRACTION_BACKEND=llm; on the default
    # gliner backend ingest doesn't touch Groq at all.
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Graph building (entity/relationship + keyword extraction). Ingest speed is
    # capped by tokens-per-minute, not by model speed, so extraction goes on
    # whichever model has the LARGER free-tier TPM budget: 70b-versatile gets
    # 12,000 TPM where 8b-instant only gets 6,000.
    groq_extract_model: str = os.getenv(
        "GROQ_EXTRACT_MODEL", "llama-3.3-70b-versatile"
    )

    # Starting size of the extraction token budget. Groq's 429 body reports the
    # real ceiling, so a wrong value here self-corrects on the first 429.
    groq_tpm_limit: int = int(os.getenv("GROQ_TPM_LIMIT", "12000"))

    # On a Groq 429, retry Groq itself this many extra times (beyond
    # lightrag's built-in 3-attempt/4-10s backoff) before giving up and
    # falling back to OpenRouter/Ollama. Groq's per-model rate limit window
    # is ~60s, so waiting it out is far faster than a CPU-bound Ollama call.
    groq_rate_limit_retries: int = int(os.getenv("GROQ_RATE_LIMIT_RETRIES", "2"))
    groq_rate_limit_wait_seconds: float = float(
        os.getenv("GROQ_RATE_LIMIT_WAIT_SECONDS", "20")
    )

    # Once a model hits a tokens-per-day 429, skip it for this long instead of
    # re-discovering the same daily-quota exhaustion on every remaining chunk.
    # Deliberately shorter than the ~30-60min TPD reset window a busy day sees
    # in practice, so quota freed by usage dropping off gets picked back up.
    groq_daily_quota_cooldown_seconds: float = float(
        os.getenv("GROQ_DAILY_QUOTA_COOLDOWN_SECONDS", "300")
    )

    # Answering fallback only (kept on a separate key/provider from Groq).
    # OpenRouter's free-tier auto-router queues behind heavy free-tier
    # demand, so it now sits behind Groq rather than in front of it.
    openrouter_api_key: str | None = os.getenv("OPENROUTER_API_KEY") or None
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openrouter/free")

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")

    embedding_model: str = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    embedding_dim: int = int(os.getenv("EMBEDDING_DIM", "384"))

    storage_dir: str = os.getenv("STORAGE_DIR", "./storage")

    # Article scraping. Blank key = trafilatura-only (zero-code rollback).
    # js_render/premium_proxy cost extra credits, so they're off for the first
    # attempt and only used on the automatic retry.
    zenrows_api_key: str | None = os.getenv("ZENROWS_API_KEY") or None
    zenrows_timeout_seconds: float = float(os.getenv("ZENROWS_TIMEOUT_SECONDS", "25"))
    zenrows_js_render_default: bool = (
        os.getenv("ZENROWS_USE_JS_RENDER_DEFAULT", "false").lower() == "true"
    )
    zenrows_premium_proxy_default: bool = (
        os.getenv("ZENROWS_USE_PREMIUM_PROXY_DEFAULT", "false").lower() == "true"
    )
    scraped_articles_dir: str = os.getenv(
        "SCRAPED_ARTICLES_DIR", "./storage/scraped_articles"
    )

    # 1024 halves the chunk count (and so the extraction calls) versus 512.
    chunk_token_size: int = int(os.getenv("CHUNK_TOKEN_SIZE", "1024"))
    chunk_overlap_token_size: int = int(os.getenv("CHUNK_OVERLAP_TOKEN_SIZE", "100"))

    # Extraction gleaning re-runs entity extraction per chunk to catch missed
    # entities, doubling LLM calls (and token cost) per chunk. Default to 0
    # (skip it) since it's what was burning through the Groq daily quota.
    entity_extract_max_gleaning: int = int(os.getenv("MAX_GLEANING", "0"))
    # How many chunks' extraction calls run concurrently. A 1024-token chunk's
    # JSON extraction call runs ~5,000 tokens against a 12,000 TPM bucket, so 2
    # in flight is what fits. The token bucket paces the rest.
    llm_model_max_async: int = int(os.getenv("MAX_ASYNC_LLM", "2"))

    # Graph extraction backend. "gliner" runs a local encoder (one forward pass
    # per window, no API, no rate limit); "llm" is the original Groq/Ollama
    # per-chunk JSON extraction, kept as a fallback for environments where the
    # model can't be downloaded.
    extraction_backend: str = os.getenv("EXTRACTION_BACKEND", "gliner").lower()
    gliner_model: str = os.getenv("GLINER_MODEL", "urchade/gliner_small-v2.1")
    gliner_threshold: float = float(os.getenv("GLINER_THRESHOLD", "0.4"))
    gliner_batch_size: int = int(os.getenv("GLINER_BATCH_SIZE", "8"))
    gliner_use_gpu: bool = os.getenv("GLINER_USE_GPU", "false").lower() == "true"
    # torch already saturates every core inside one batch, so this is about
    # overlapping one chunk's tokenization with another's matmul, not cores.
    gliner_max_async: int = int(os.getenv("GLINER_MAX_ASYNC", "2"))

    # The ontology: the closed set of types the extractor is scored against on
    # every chunk. Keep it short and deliberate -- that's what keeps entity
    # types consistent across a document.
    entity_labels: list[str] = [
        label.strip()
        for label in os.getenv(
            "ENTITY_LABELS",
            "person,organization,location,product,technology,event,concept,date",
        ).split(",")
        if label.strip()
    ]

    # Hard ceiling on the context LightRAG assembles per chat query (entities +
    # relations + chunks + system prompt). LightRAG's own default is 30,000,
    # which no free-tier Groq model can accept in one minute. Must stay below
    # the query model's TPM limit with headroom for the answer itself.
    query_context_token_budget: int = int(
        os.getenv("QUERY_CONTEXT_TOKEN_BUDGET", "8000")
    )

    # Cross-encoder re-ranking of retrieved chunks before the context is
    # assembled. Local, no API. Matters because the budget above decides how
    # many chunks survive -- this decides which ones.
    rerank_enabled: bool = os.getenv("RERANK_ENABLED", "true").lower() == "true"
    rerank_model: str = os.getenv(
        "RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
    )
    rerank_top_n: int = int(os.getenv("RERANK_TOP_N", "10"))

    cors_origins: list[str] = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]

    @property
    def kb_working_dir(self) -> str:
        return os.path.join(self.storage_dir, "kb")

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.storage_dir, "manifest.sqlite3")


@lru_cache
def get_settings() -> Settings:
    return Settings()
