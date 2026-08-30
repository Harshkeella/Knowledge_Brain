"""Call edges have to be right or absent -- never wrong.

A CALLS edge nothing can distinguish from a correct one is worse than a gap,
so the cases here are the two that matter: a name that resolves to exactly one
target does get an edge, and a name that could be several does not.
"""

import asyncio

import pytest

from app.services import code_intel, folder_ingest, manifest
from app.services import graph_schema as gs
from app.services.test_source_graph import FakeRag

UTIL = '''
def helper():
    return 1


def only_here():
    return 2
'''

APP = '''
import json

from util import helper


class Base:
    def save(self):
        return helper()


class Child(Base):
    def save(self):
        return 0


def main():
    return only_here()


def caller():
    return save()


def uses_lib():
    json.dumps({})
    json.dumps({})
    return len([])
'''


@pytest.fixture
def ingested(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "util.py").write_text(UTIL, encoding="utf-8")
    (proj / "src" / "app.py").write_text(APP, encoding="utf-8")

    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path / "storage"))
    asyncio.run(manifest.init_db())
    rag = FakeRag()

    async def fake_get_rag():
        return rag

    monkeypatch.setattr(folder_ingest, "get_rag", fake_get_rag)
    result = asyncio.run(
        folder_ingest.ingest_folder(str(proj), name="proj", index_documents=False)
    )
    return rag, result


APP_NODE = "proj/src/app.py"
UTIL_NODE = "proj/src/util.py"


def test_symbols_become_nodes_with_the_right_labels(ingested):
    rag, result = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    assert nodes[f"{APP_NODE}::Base"]["entity_type"] == gs.CLASS
    assert nodes[f"{APP_NODE}::Base.save"]["entity_type"] == gs.METHOD
    assert nodes[f"{APP_NODE}::main"]["entity_type"] == gs.FUNCTION
    assert nodes[f"{UTIL_NODE}::helper"]["signature"] == "helper()"
    assert nodes[f"{APP_NODE}::Base.save"]["class_qualified_name"] == "Base"
    assert result["classes"] == 2 and result["methods"] == 2
    assert nodes[APP_NODE]["loc"] > 0


def test_definitions_hang_off_the_file_and_methods_off_the_class(ingested):
    rag, _ = ingested
    edges = rag.chunk_entity_relation_graph.edges
    assert edges[(APP_NODE, f"{APP_NODE}::Base")]["keywords"] == gs.DEFINES
    assert edges[(APP_NODE, f"{APP_NODE}::main")]["keywords"] == gs.DEFINES
    assert (
        edges[(f"{APP_NODE}::Base", f"{APP_NODE}::Base.save")]["keywords"]
        == gs.DEFINES_METHOD
    )
    # A method is defined by its class, not a second time by the file.
    assert (APP_NODE, f"{APP_NODE}::Base.save") not in edges
    assert edges[(f"{APP_NODE}::Child", f"{APP_NODE}::Base")]["keywords"] == gs.INHERITS


def test_call_direction_survives_the_undirected_store(ingested):
    """The store keeps node PAIRS, not order. Direction has to ride on the edge
    or "A calls B" reads back as "B calls A"."""
    rag, _ = ingested
    edge = rag.chunk_entity_relation_graph.edges[
        (f"{APP_NODE}::Base.save", f"{UTIL_NODE}::helper")
    ]
    assert edge["rel_from"] == f"{APP_NODE}::Base.save"
    assert edge["rel_to"] == f"{UTIL_NODE}::helper"


def test_an_unambiguous_call_resolves_across_files(ingested):
    rag, _ = ingested
    edges = rag.chunk_entity_relation_graph.edges
    # Base.save() calls helper(), which exists once in the whole project.
    assert edges[(f"{APP_NODE}::Base.save", f"{UTIL_NODE}::helper")]["keywords"] == (
        gs.CALLS
    )
    assert (f"{APP_NODE}::main", f"{UTIL_NODE}::only_here") in edges
    assert edges[(APP_NODE, UTIL_NODE)]["keywords"] == gs.IMPORTS


def test_an_ambiguous_call_is_recorded_not_guessed(ingested):
    rag, result = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    edges = rag.chunk_entity_relation_graph.edges
    # `save` is defined on both Base and Child, so caller() -> save() has two
    # candidates and must get neither edge.
    caller = nodes[f"{APP_NODE}::caller"]
    assert "save" in caller["calls_unresolved"]
    assert not [
        pair
        for pair in edges
        if pair[0] == f"{APP_NODE}::caller" and edges[pair]["keywords"] == gs.CALLS
    ]
    assert result["unresolved"] >= 1


def test_code_symbols_stay_out_of_the_entity_index(ingested):
    rag, _ = ingested
    indexed = {record["entity_name"] for record in rag.entities_vdb.records.values()}
    assert not any("::" in name for name in indexed), "symbols are graph-only"
    assert APP_NODE in indexed, "the file itself is still retrievable"


def test_a_file_that_will_not_parse_does_not_abort_the_walk():
    broken = code_intel.extract("def (((:", "python")
    assert broken.symbols == [] and broken.imports == []


def test_javascript_needs_no_grammar_to_stay_quiet():
    # Without the optional tree-sitter extra this returns empty rather than
    # raising; with it, real symbols. Either way the ingest survives.
    result = code_intel.extract("export function a(){ return b(); }", "javascript")
    assert isinstance(result, code_intel.FileSymbols)
    if result.symbols:
        assert result.symbols[0].name == "a"
        assert result.symbols[0].calls == ["b"]


def test_the_write_is_committed(ingested):
    """Symbols are written last in the ingest, so they are the first thing lost
    when nothing persists. This is the assertion that the loss cannot recur."""
    rag, _ = ingested
    assert rag.chunk_entity_relation_graph.flushed >= 1


def test_a_library_call_becomes_a_visible_edge_not_a_string(ingested):
    rag, result = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    edges = rag.chunk_entity_relation_graph.edges

    external = nodes["external:json.dumps"]
    assert external["entity_type"] == gs.EXTERNAL_SYMBOL
    assert external["module_guess"] == "json" and external["external"] is True

    edge = edges[(f"{APP_NODE}::uses_lib", "external:json.dumps")]
    assert edge["keywords"] == gs.CALLS
    assert edge["resolved"] is False and edge["confidence"] == 0.0
    assert edge["call_count"] == 2, "two call sites collapse onto one pair"
    assert edge["call_site_line"] > 0
    assert result["external_symbols"] >= 1


def test_builtins_never_become_nodes(ingested):
    rag, _ = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    assert "external:len" not in nodes, "a `len` hub would drown the call graph"
    # Still recorded, just not as a node.
    assert "len" in nodes[f"{APP_NODE}::uses_lib"]["calls_unresolved"]


def test_call_counts_are_precomputed_on_the_nodes(ingested):
    rag, _ = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    # helper() is called by Base.save; nothing calls only_here except main.
    assert nodes[f"{UTIL_NODE}::helper"]["calls_in_count"] == 1
    assert nodes[f"{UTIL_NODE}::helper"]["calls_out_count"] == 0
    assert nodes[f"{APP_NODE}::main"]["calls_out_count"] == 1
    assert nodes[f"{APP_NODE}::uses_lib"]["calls_out_count"] == 1


def test_edge_categories_cover_every_edge_written(ingested):
    rag, _ = ingested
    edges = rag.chunk_entity_relation_graph.edges
    seen = {gs.edge_category(d["keywords"]) for d in edges.values()}
    assert seen == {gs.STRUCTURAL, gs.BEHAVIORAL}
    assert gs.edge_category(gs.CALLS) == gs.BEHAVIORAL
    assert gs.edge_category(gs.DEFINES) == gs.STRUCTURAL
