"""Hybrid retrieval: a query whose dense signal points at the wrong document
must still return the right one when it names an exact term.

That is the whole reason for the sparse vector, so it is what this checks --
against a real embedded Qdrant, not a mock, since the fusion happens inside it.
"""

import asyncio

import numpy as np
import pytest
from lightrag.kg.shared_storage import initialize_share_data
from lightrag.utils import EmbeddingFunc

DOCS = {
    "revenue": "Quarterly revenue report for the fiscal year",
    "earnings": "Annual earnings summary and financial results",
    "warranty": "Serial number XJ-9920-B was replaced under warranty",
}

# Deliberately misleading: the two finance documents sit close together and the
# warranty note sits far away, so a dense-only search for the serial number
# ranks it last no matter how the query is embedded.
VECTORS = {
    "Quarterly revenue report for the fiscal year": [1.0, 0.0, 0.0],
    "Annual earnings summary and financial results": [0.95, 0.31, 0.0],
    "Serial number XJ-9920-B was replaced under warranty": [0.0, 0.0, 1.0],
}


async def _embed(texts, **kwargs):
    return np.array([VECTORS[t] for t in texts], dtype=np.float32)


def _storage(namespace: str):
    from app.services.qdrant_store import HybridQdrantStorage

    return HybridQdrantStorage(
        namespace=namespace,
        workspace="test",
        global_config={
            "vector_db_storage_cls_kwargs": {"cosine_better_than_threshold": 0.2},
            "embedding_batch_num": 8,
        },
        embedding_func=EmbeddingFunc(embedding_dim=3, func=_embed),
        meta_fields={"content"},
    )


async def _run(store) -> tuple[list[str], list[str]]:
    await store.initialize()
    await store.upsert(
        {key: {"content": text} for key, text in DOCS.items()}
    )
    await store.index_done_callback()

    # The query embedding is pinned to the finance neighbourhood: dense alone
    # cannot surface the warranty note.
    finance_vector = VECTORS[DOCS["revenue"]]
    hybrid = await store.query("XJ-9920-B", top_k=3, query_embedding=finance_vector)
    dense_only = await store.query("", top_k=3, query_embedding=finance_vector)
    return [r["id"] for r in hybrid], [r["id"] for r in dense_only]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the store at a temp directory.

    `monkeypatch.setenv` does NOT work here: Settings reads every env var once,
    at import, so the store would silently stay on the real ./storage -- which
    is exactly how a 3-dimensional test collection once shadowed the live one
    and broke every ingest with a numpy broadcast error. Patch the object.
    """
    from app.services import qdrant_store

    monkeypatch.setattr(qdrant_store._settings, "storage_dir", str(tmp_path))
    monkeypatch.setattr(qdrant_store, "_client", None)
    initialize_share_data()
    yield qdrant_store
    qdrant_store.close_client()


def test_exact_term_outranks_a_misleading_dense_signal(store):
    hybrid, dense_only = asyncio.run(_run(_storage("chunks")))

    # RRF is rank-based, so the sparse branch's only hit ties with the dense
    # branch's top hit rather than beating it -- the cross-encoder rerank that
    # runs downstream breaks that tie. What matters here is that the warranty
    # note is retrieved at all, and above the document dense preferred to it.
    assert "warranty" in hybrid[:2], (
        f"the exact serial number must surface the warranty note; got {hybrid}"
    )
    assert hybrid.index("warranty") < hybrid.index("earnings"), (
        f"an exact term must outrank a merely-similar document; got {hybrid}"
    )
    assert "warranty" not in dense_only, (
        f"dense alone cannot find it -- that is the point; got {dense_only}"
    )


def test_a_wrong_dimension_collection_is_rebuilt_not_written_into(store):
    """The failure this replaces surfaced as a numpy broadcast error at flush,
    after a whole document had already been extracted and merged."""
    from qdrant_client import models

    name = "lightrag_vdb_chunks"
    client = store.get_client()
    client.create_collection(
        name,
        vectors_config={"dense": models.VectorParams(size=99, distance="Cosine")},
    )
    # Points, not just a config: embedded Qdrant deletes a collection with
    # `rmtree(ignore_errors=True)` while still holding its sqlite open, so on
    # Windows the data outlives the drop and gets reloaded under the new
    # config -- 384d declared over 99d vectors, which only fails at flush.
    client.upsert(
        name,
        points=[
            models.PointStruct(id=i, vector={"dense": [0.0] * 99}, payload={})
            for i in range(3)
        ],
        wait=True,
    )

    hybrid, _ = asyncio.run(_run(_storage("chunks")))

    assert "warranty" in hybrid, "ingest must survive a stale collection"
    info = client.get_collection(name)
    assert info.config.params.vectors["dense"].size == 3
    assert "sparse" in (info.config.params.sparse_vectors or {})
    stored, _ = client.scroll(name, limit=1, with_vectors=True)
    assert len(stored[0].vector["dense"]) == 3, "the old points must be gone"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
