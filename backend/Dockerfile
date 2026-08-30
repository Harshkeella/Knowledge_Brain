# One image for the API. There is no separate worker image: ingestion runs in
# the same process (see docs/DEPLOYMENT.md), and a second image that differs
# only by its command is a build to keep in sync for nothing.
FROM python:3.12-slim

# libgomp: torch's OpenMP runtime, needed by sentence-transformers and GLiNER.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so a source edit does not re-download torch.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Models are NOT baked in: ~500MB of weights would make every push slow and
# every rollback slower. Mount a volume here and the first boot fills it once.
ENV HF_HOME=/models \
    STORAGE_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data", "/models"]

# Non-root, and it must own the volumes it writes.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data /models \
    && chown -R app:app /app /data /models
USER app

EXPOSE 8000

# Cheap, and never an LLM call -- see main.health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# One worker on purpose. Embedded Qdrant takes an exclusive lock on its
# directory and LightRAG's file storages assume a single writer per workspace,
# so a second worker would fight the first for both. Concurrency comes from
# asyncio within the process; scale by making the box bigger, or move Qdrant
# to a server (QDRANT_URL) before adding workers.
#
# --timeout-graceful-shutdown lets an in-flight ingest finish and the storages
# flush, instead of leaving a half-written graph behind.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-graceful-shutdown", "30"]
