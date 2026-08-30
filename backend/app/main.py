import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, graph, ingest, knowledge_base, me
from app.core import auth
from app.core.config import get_settings
from app.services.lightrag_engine import get_rag, shutdown_rag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Per-user stores are created by the first authenticated request (see
    # auth.ensure_stores), so there is nothing user-shaped to open at boot on a
    # multi-user deployment -- there is no user yet. The single-user install
    # still gets its warm LightRAG, because there the user is known.
    if settings.auth_disabled:
        local = auth.User(id=settings.local_user_id)
        auth.run_as(local)
        await auth.ensure_stores(local)
        await get_rag()

    # Model loads, paid once at boot instead of by the first upload/message.
    # Process-global and user-independent, so they warm up either way.
    import asyncio

    # Prefetch only: a download failure (offline, HF hiccup) must not kill boot.
    try:
        if settings.extraction_backend == "gliner":
            from app.services.gliner_extract import warmup as warm_extractor

            await asyncio.to_thread(warm_extractor)
        if settings.rerank_enabled:
            from app.services.reranker import warmup as warm_reranker

            await asyncio.to_thread(warm_reranker)
    except Exception:
        logging.getLogger("app.main").warning(
            "Model warmup failed; models will load on first use", exc_info=True
        )
    yield
    await shutdown_rag()


app = FastAPI(title="nodeRels GraphRAG Knowledge Base API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(me.router)
app.include_router(ingest.router)
app.include_router(knowledge_base.router)
app.include_router(chat.router)
app.include_router(graph.router)


@app.get("/health")
async def health():
    """Liveness plus the dependencies a request actually needs.

    Deliberately cheap: it opens the vector store and touches the storage
    directory, and never makes an LLM call -- a health check that costs a
    generation is one an outage turns into a bill.
    """
    checks: dict[str, str] = {}

    try:
        from app.services import qdrant_store

        qdrant_store.get_client().get_collections()
        checks["qdrant"] = "ok"
    except Exception as e:
        checks["qdrant"] = f"error: {type(e).__name__}"

    try:
        os.makedirs(settings.storage_dir, exist_ok=True)
        probe = os.path.join(settings.storage_dir, ".health")
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {type(e).__name__}"

    checks["auth"] = "disabled" if settings.auth_disabled else (
        "ok" if settings.supabase_jwt_secret else "error: no JWT secret"
    )

    degraded = [k for k, v in checks.items() if v.startswith("error")]
    return {
        "status": "degraded" if degraded else "ok",
        "checks": checks,
    }
