import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, graph, ingest, knowledge_base
from app.core.config import get_settings
from app.services import manifest
from app.services.lightrag_engine import get_rag, shutdown_rag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manifest.init_db()
    await get_rag()
    # Model loads, paid once at boot instead of by the first upload/message.
    import asyncio

    if settings.extraction_backend == "gliner":
        from app.services.gliner_extract import warmup as warm_extractor

        await asyncio.to_thread(warm_extractor)
    if settings.rerank_enabled:
        from app.services.reranker import warmup as warm_reranker

        await asyncio.to_thread(warm_reranker)
    yield
    await shutdown_rag()


app = FastAPI(title="GraphRAG Knowledge Base API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(knowledge_base.router)
app.include_router(chat.router)
app.include_router(graph.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
