"""One node per upload, linked to exactly that upload's output.

The trap this guards is the one that makes provenance quietly wrong: matching
a document's nodes by substring, so "report.pdf" adopts everything belonging
to "old report.pdf".
"""

import asyncio

import pytest
from lightrag.constants import GRAPH_FIELD_SEP

from app.services import graph_schema as gs
from app.services import source_graph


class FakeGraph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str], dict] = {}

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def upsert_node(self, node_id, node_data):
        self.nodes[node_id] = node_data

    async def upsert_edge(self, source, target, edge_data):
        self.edges[(source, target)] = edge_data

    async def remove_nodes(self, node_ids):
        for node_id in node_ids:
            self.nodes.pop(node_id, None)
        self.edges = {
            pair: data
            for pair, data in self.edges.items()
            if not set(pair) & set(node_ids)
        }

    async def get_all_nodes(self):
        return [{**data, "id": node_id} for node_id, data in self.nodes.items()]

    # The store is undirected: an incident edge comes back whichever way it
    # was written, which is what makes a one-hop expand see both directions.
    async def has_node(self, node_id):
        return node_id in self.nodes

    async def get_node_edges(self, node_id):
        return [pair for pair in self.edges if node_id in pair]

    async def get_edge(self, source, target):
        return self.edges.get((source, target)) or self.edges.get((target, source))

    async def node_degree(self, node_id):
        return sum(1 for pair in self.edges if node_id in pair)

    async def index_done_callback(self):
        # Real storage commits to disk here; the fake only records that the
        # caller remembered to ask, which is the thing that was broken.
        self.flushed = getattr(self, "flushed", 0) + 1


class FakeVdb:
    def __init__(self):
        self.records: dict[str, dict] = {}

    async def upsert(self, data):
        self.records.update(data)

    async def delete(self, ids):
        for record_id in ids:
            self.records.pop(record_id, None)

    async def index_done_callback(self):
        self.flushed = getattr(self, "flushed", 0) + 1


class FakeRag:
    def __init__(self):
        self.chunk_entity_relation_graph = FakeGraph()
        self.entities_vdb = FakeVdb()


RECORD = {
    "doc_id": "doc-1",
    "file_name": "report.pdf",
    "source_type": "pdf",
    "chunk_count": 3,
    "size_bytes": 2048,
    "date_added": "2026-08-23T10:00:00+00:00",
}


@pytest.fixture
def registered():
    rag = FakeRag()
    graph = rag.chunk_entity_relation_graph
    graph.nodes["ACME CORP"] = {"entity_type": "organization", "file_path": "report.pdf"}
    # Same entity, seen in two documents: LightRAG joins the names with <SEP>.
    graph.nodes["GLOBEX"] = {
        "entity_type": "organization",
        "file_path": f"contract.md{GRAPH_FIELD_SEP}report.pdf",
    }
    # The substring trap. This must NOT be adopted by report.pdf.
    graph.nodes["INITECH"] = {
        "entity_type": "organization",
        "file_path": "old report.pdf",
    }
    asyncio.run(source_graph.register(rag, RECORD))
    return rag


def test_the_write_is_actually_committed(registered):
    """LightRAG's storage: "Callers outside the pipeline must persist
    explicitly." Nothing here goes through ainsert(), so without this the
    supernode lives in memory and dies with the process."""
    assert registered.chunk_entity_relation_graph.flushed >= 1
    assert registered.entities_vdb.flushed >= 1


def test_supernode_carries_the_upload_metadata(registered):
    node = registered.chunk_entity_relation_graph.nodes["source:report.pdf"]
    assert node["entity_type"] == gs.SOURCE
    assert gs.SOURCE in gs.NODE_LABELS
    assert node["source_type"] == "pdf"
    assert node["chunk_count"] == 3 and node["size_bytes"] == 2048
    assert node["ingested_at"] == RECORD["date_added"]


def test_links_only_this_upload_s_nodes(registered):
    edges = registered.chunk_entity_relation_graph.edges
    linked = {target for source, target in edges if source == "source:report.pdf"}
    assert linked == {"ACME CORP", "GLOBEX"}, "INITECH belongs to 'old report.pdf'"
    assert all(data["keywords"] == gs.HAS_ROOT for data in edges.values())


def test_attach_is_idempotent_and_never_self_links(registered):
    graph = registered.chunk_entity_relation_graph
    before = len(graph.edges)
    asyncio.run(source_graph.attach(registered, RECORD, ["ACME CORP", "source:report.pdf"]))
    assert len(graph.edges) == before


def test_remove_drops_the_node_its_edges_and_its_vector_record(registered):
    asyncio.run(source_graph.remove(registered, "report.pdf"))
    graph = registered.chunk_entity_relation_graph
    assert "source:report.pdf" not in graph.nodes
    assert graph.edges == {}
    # The document's own entities are LightRAG's to delete, not ours.
    assert "ACME CORP" in graph.nodes
    assert registered.entities_vdb.records == {}
