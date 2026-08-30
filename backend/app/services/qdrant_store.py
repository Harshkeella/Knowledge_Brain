"""Qdrant vector storage for LightRAG, with dense + sparse hybrid retrieval.

LightRAG ships a Qdrant backend, but it is dense-only: one unnamed cosine
vector per point. That is exactly the half of retrieval MiniLM is worst at --
a part number, an error code, a column header, a surname -- so this subclass
gives every point two named vectors, "dense" (the embedding model) and
"sparse" (BM25 term ids, see app.services.sparse), and answers a query with
one Qdrant Query API call that fuses both branches with RRF.

Only four methods change; buffering, deletes, workspace isolation and
read-your-writes are inherited untouched.
"""

import asyncio
import logging
from typing import Any

import numpy as np
from qdrant_client import QdrantClient, models

from lightrag.constants import DEFAULT_QUERY_PRIORITY
from lightrag.kg.qdrant_impl import (
    CREATED_AT_FIELD,
    QdrantVectorDBStorage,
    compute_mdhash_id_for_qdrant,
    workspace_filter_condition,
)
from lightrag.kg.shared_storage import get_data_init_lock, get_namespace_lock

from app.core.config import get_settings
from app.services import sparse

logger = logging.getLogger("app.qdrant_store")
_settings = get_settings()

DENSE = "dense"
SPARSE = "sparse"

# Embedded Qdrant takes an exclusive lock on its directory, and LightRAG builds
# one storage instance per namespace (chunks/entities/relationships). They have
# to share a client or the second one fails to open the store.
_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        if _settings.qdrant_url:
            logger.info("Connecting to Qdrant at %s", _settings.qdrant_url)
            _client = QdrantClient(
                url=_settings.qdrant_url, api_key=_settings.qdrant_api_key
            )
        else:
            logger.info("Opening embedded Qdrant at %s", _settings.qdrant_path)
            _client = QdrantClient(path=_settings.qdrant_path)
    return _client


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def _sparse_vector(indices: list[int], values: list[float]) -> models.SparseVector:
    return models.SparseVector(indices=indices, values=values)


class HybridQdrantStorage(QdrantVectorDBStorage):
    def _stale_collection(self, name: str) -> str | None:
        """Why an existing collection can't be used, or None if it's fine.

        Dimension is what bites: LightRAG names collections after the namespace
        only, so a store written by a different embedding model -- or by a test
        that leaked into the real storage directory -- looks valid right up
        until numpy refuses to broadcast a 384d vector into a 3d slot,
        mid-flush, after a whole document has already been extracted.

        The declared config is checked AND a real stored vector is measured,
        because the two can disagree: see _drop_collection.
        """
        if not self._client.collection_exists(name):
            return None

        wanted = self.embedding_func.embedding_dim
        params = self._client.get_collection(name).config.params
        vectors = params.vectors
        if not isinstance(vectors, dict) or DENSE not in vectors:
            return "it has no named 'dense' vector (a dense-only build wrote it)"
        if vectors[DENSE].size != wanted:
            return (
                f"its dense vectors are {vectors[DENSE].size}d but the "
                f"embedding model produces {wanted}d"
            )
        if not (params.sparse_vectors or {}).get(SPARSE):
            return "it has no sparse vector, so keyword search cannot work"

        points, _ = self._client.scroll(name, limit=1, with_vectors=True)
        stored = points[0].vector if points else None
        dense = stored.get(DENSE) if isinstance(stored, dict) else None
        if dense is not None and len(dense) != wanted:
            return (
                f"it claims {wanted}d but the vectors actually stored in it "
                f"are {len(dense)}d (a previous drop left its data behind)"
            )
        return None

    def _drop_collection(self, name: str) -> None:
        """Delete a collection *and* its data.

        Embedded Qdrant's delete_collection does
        `shutil.rmtree(path, ignore_errors=True)` while still holding the
        collection's sqlite file open. On Windows that unlink fails, the error
        is swallowed, and the next create_collection reloads the OLD points
        from the surviving file -- leaving a collection whose config says 384d
        over vectors that are still 3d. Closing the handle first is what makes
        the delete real. Harmless no-op against a Qdrant server.
        """
        collection = getattr(self._client._client, "collections", {}).get(name)
        if hasattr(collection, "close"):
            collection.close()
        self._client.delete_collection(name)

    async def initialize(self) -> None:
        async with get_data_init_lock():
            if self._initialized:
                return
            self._client = get_client()
            name = self.final_namespace

            stale = self._stale_collection(name)
            if stale is not None:
                # A collection whose vectors are the wrong shape cannot be
                # written to OR queried at the current embedding size, so
                # there is nothing in it left to preserve. Recreating beats
                # failing every flush with a numpy broadcast error.
                logger.warning(
                    "Recreating Qdrant collection %s: %s. Everything it held "
                    "was unreadable at %dd and has to be re-ingested.",
                    name,
                    stale,
                    self.embedding_func.embedding_dim,
                )
                self._drop_collection(name)

            if not self._client.collection_exists(name):
                self._client.create_collection(
                    name,
                    vectors_config={
                        DENSE: models.VectorParams(
                            size=self.embedding_func.embedding_dim,
                            distance=models.Distance.COSINE,
                        )
                    },
                    # IDF is what turns raw term frequencies into keyword
                    # relevance, and Qdrant computes it from the collection --
                    # so nothing here has to track corpus statistics.
                    sparse_vectors_config={
                        SPARSE: models.SparseVectorParams(
                            modifier=models.Modifier.IDF
                        )
                    },
                    hnsw_config=models.HnswConfigDiff(payload_m=16, m=0),
                )
                logger.info("Created hybrid Qdrant collection %s", name)

            # Boot is the only place this is cheap to catch. Every alternative
            # surfaces it as a numpy error at flush, after a document has
            # already been chunked, extracted and merged.
            broken = self._stale_collection(name)
            if broken is not None:
                raise RuntimeError(
                    f"Qdrant collection {name!r} is unusable: {broken}. Stop "
                    f"the server, delete {_settings.qdrant_path!r}, and "
                    f"re-ingest."
                )

            # Multi-tenant payload index. Embedded Qdrant has no payload
            # indexes at all (it warns and ignores this), and filtering still
            # works there without one -- it is a server-side speedup only.
            if _settings.qdrant_url:
                self._client.create_payload_index(
                    collection_name=name,
                    field_name="workspace_id",
                    field_schema=models.KeywordIndexParams(
                        type=models.KeywordIndexType.KEYWORD, is_tenant=True
                    ),
                )
            self._initialized = True

        if self._flush_lock is None:
            self._flush_lock = get_namespace_lock(
                namespace=self.final_namespace,
                workspace=self.effective_workspace,
            )

    async def _flush_pending_vector_ops(self) -> None:
        """Embed and write buffered points, sparse vector included.

        Replaces the parent's version rather than extending it: the parent
        builds PointStruct(vector=<list>) inline, and a named-vector point has
        to be built at the same place the embedding lands.

        ponytail: batches by point count only (the parent also estimates
        payload bytes). At a 1024-token chunk that is ~0.5MB per batch against
        a 16MB ceiling. Restore the byte estimate if payloads ever get large.
        """
        async with self._flush_lock:
            if not self._pending_vector_docs and not self._pending_vector_deletes:
                return
            if self._client is None:
                return

            pending = self._pending_vector_docs
            deletes = self._pending_vector_deletes

            todo = [(k, d) for k, d in pending.items() if d.vector is None]
            if todo:
                contents = [d.content for _, d in todo]
                size = self._max_batch_size
                batches = [
                    contents[i : i + size] for i in range(0, len(contents), size)
                ]
                embeddings = np.concatenate(
                    await asyncio.gather(
                        *[self.embedding_func(b, context="document") for b in batches]
                    )
                )
                if len(embeddings) != len(todo):
                    raise RuntimeError(
                        f"[{self.workspace}] Embedding count mismatch: expected "
                        f"{len(todo)}, got {len(embeddings)}"
                    )
                for (_, doc), embedding in zip(todo, embeddings):
                    doc.vector = np.asarray(embedding, dtype=np.float32).tolist()

            committed = [k for k, d in pending.items() if d.vector is not None]
            points = [
                models.PointStruct(
                    id=compute_mdhash_id_for_qdrant(
                        doc_id, prefix=self.effective_workspace
                    ),
                    vector={
                        DENSE: pending[doc_id].vector,
                        SPARSE: _sparse_vector(
                            *sparse.encode_document(pending[doc_id].content)
                        ),
                    },
                    payload=dict(pending[doc_id].source),
                )
                for doc_id in committed
            ]

            # Any failure here raises with both buffers intact, so the next
            # index_done_callback retries -- same contract as the parent.
            step = self._max_upsert_points_per_batch or len(points)
            for i in range(0, len(points), step):
                self._client.upsert(
                    collection_name=self.final_namespace,
                    points=points[i : i + step],
                    wait=True,
                )

            if deletes:
                ids = [
                    compute_mdhash_id_for_qdrant(
                        doc_id, prefix=self.effective_workspace
                    )
                    for doc_id in deletes
                ]
                step = self._max_delete_points_per_batch or len(ids)
                for i in range(0, len(ids), step):
                    self._client.delete(
                        collection_name=self.final_namespace,
                        points_selector=models.PointIdsList(points=ids[i : i + step]),
                        wait=True,
                    )

            for doc_id in committed:
                pending.pop(doc_id, None)
            deletes.clear()

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] = None
    ) -> list[dict[str, Any]]:
        """Dense and sparse branches fused by RRF, server-side, in one call.

        The cosine threshold stays on the dense branch only: it is a cosine
        number and means nothing against a BM25 score, and a rare exact term
        should be able to rank on the sparse branch alone.
        """
        if query_embedding is None:
            result = await self.embedding_func(
                [query], context="query", _priority=DEFAULT_QUERY_PRIORITY
            )
            query_embedding = result[0]

        workspace = models.Filter(
            must=[workspace_filter_condition(self.effective_workspace)]
        )
        depth = top_k * _settings.hybrid_prefetch_multiplier

        prefetch = [
            models.Prefetch(
                query=list(query_embedding),
                using=DENSE,
                limit=depth,
                filter=workspace,
                score_threshold=self.cosine_better_than_threshold,
            )
        ]
        indices, values = sparse.encode_query(query)
        if indices:
            prefetch.append(
                models.Prefetch(
                    query=_sparse_vector(indices, values),
                    using=SPARSE,
                    limit=depth,
                    filter=workspace,
                )
            )

        points = self._client.query_points(
            collection_name=self.final_namespace,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=top_k,
            with_payload=True,
            query_filter=workspace,
        ).points

        return [
            {
                **point.payload,
                # An RRF score, not a cosine one. Nothing downstream in
                # LightRAG reads this field; it is kept for parity.
                "distance": point.score,
                CREATED_AT_FIELD: point.payload.get(CREATED_AT_FIELD),
            }
            for point in points
        ]

    async def get_vectors_by_ids(self, ids: list[str]) -> dict[str, list[float]]:
        """Named vectors come back as a dict; callers want the dense one."""
        vectors = await super().get_vectors_by_ids(ids)
        return {
            k: (v[DENSE] if isinstance(v, dict) else v) for k, v in vectors.items()
        }


def register() -> str:
    """Make LightRAG able to resolve this class by name, and return that name.

    LightRAG resolves storage backends through three module-level registries
    keyed by class name; adding an entry to each is the whole integration.
    """
    from lightrag import kg

    name = HybridQdrantStorage.__name__
    kg.STORAGES[name] = __name__
    kg.STORAGE_ENV_REQUIREMENTS[name] = []
    implementations = kg.STORAGE_IMPLEMENTATIONS["VECTOR_STORAGE"]["implementations"]
    if name not in implementations:
        implementations.append(name)
    return name
