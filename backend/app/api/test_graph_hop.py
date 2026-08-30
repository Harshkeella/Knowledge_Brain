"""The hop view: what the graph shows before you have clicked anything, and
what one double-click is allowed to add.

The two rules worth a test are exact counts. The landing view must show
`count(Source)` nodes -- not "roughly the sources", not the sources plus
whatever high-degree neighbours came along -- and an expansion must add one
hop, both directions, with an edge id stable enough that seeing the same node
twice is a no-op in the frontend's merge.
"""

import asyncio

import pytest

from app.api import graph as graph_api
from app.services import graph_schema as gs
from app.services.test_source_graph import FakeRag


@pytest.fixture
def rag(monkeypatch):
    rag = FakeRag()
    store = rag.chunk_entity_relation_graph

    async def node(node_id, label, **props):
        await gs.upsert_node(
            rag, node_id, label, description=f"{node_id}.",
            file_path="proj", source_id="doc-1", **props,
        )

    async def edge(source, target, rel_type):
        await gs.upsert_edge(
            rag, source, target, rel_type,
            description=f"{source} -> {target}.", file_path="proj", source_id="doc-1",
        )

    async def build():
        await node("source:proj", gs.SOURCE, source_type="folder", status="completed")
        await node("source:notes.pdf", gs.SOURCE, source_type="pdf", status="completed")
        await node("proj", gs.FOLDER, path="", depth=0)
        await node("proj/src", gs.FOLDER, path="src", depth=1)
        await node("proj/src/app.py", gs.CODE_FILE, path="src/app.py", language="python")
        await node("proj/src/util.py", gs.CODE_FILE, path="src/util.py", language="python")
        await node("proj/src/app.py::main", gs.FUNCTION, calls_out_count=1, calls_in_count=0)
        await node("proj/src/util.py::helper", gs.FUNCTION, calls_out_count=0, calls_in_count=1)

        await edge("source:proj", "proj", gs.HAS_ROOT)
        await edge("proj", "proj/src", gs.CONTAINS_FOLDER)
        await edge("proj/src", "proj/src/app.py", gs.CONTAINS_FILE)
        await edge("proj/src", "proj/src/util.py", gs.CONTAINS_FILE)
        await edge("proj/src/app.py", "proj/src/app.py::main", gs.DEFINES)
        await edge("proj/src/util.py", "proj/src/util.py::helper", gs.DEFINES)
        await edge("proj/src/app.py::main", "proj/src/util.py::helper", gs.CALLS)

    asyncio.run(build())
    assert store.nodes  # the fixture built something

    async def fake_get_rag():
        return rag

    monkeypatch.setattr(graph_api, "get_rag", fake_get_rag)
    return rag


def test_the_landing_view_is_exactly_the_sources(rag):
    out = asyncio.run(graph_api.get_sources())
    assert [node.id for node in out.nodes] == ["source:proj", "source:notes.pdf"]
    assert out.edges == [], "no edges, or it is not a landing view"
    assert {node.entity_type for node in out.nodes} == {gs.SOURCE}
    assert [node.source_type for node in out.nodes] == ["folder", "pdf"]
    assert [node.status for node in out.nodes] == ["completed", "completed"]


def test_each_layer_reveals_the_next_one_and_only_the_next_one(rag):
    def hop(node_id):
        out = asyncio.run(graph_api.expand_node(node_id))
        return {node.id for node in out.nodes}, out

    revealed, out = hop("source:proj")
    assert revealed == {"proj"}, "a Source reveals its root through HAS_ROOT"
    assert [edge.keywords for edge in out.edges] == [gs.HAS_ROOT]

    revealed, _ = hop("proj/src")
    assert revealed == {"proj", "proj/src/app.py", "proj/src/util.py"}

    revealed, _ = hop("proj/src/app.py")
    assert revealed == {"proj/src", "proj/src/app.py::main"}


def test_expanding_a_function_surfaces_callers_as_well_as_callees(rag):
    out = asyncio.run(graph_api.expand_node("proj/src/util.py::helper"))
    calls = [edge for edge in out.edges if edge.keywords == gs.CALLS]
    assert len(calls) == 1
    # The store is undirected; the direction rides on the edge, so a callee
    # expanded from the wrong end still reports who called it, not the reverse.
    assert calls[0].source == "proj/src/app.py::main"
    assert calls[0].target == "proj/src/util.py::helper"
    assert calls[0].edge_category == gs.BEHAVIORAL


def test_two_paths_to_the_same_node_agree_on_its_identity(rag):
    """Dedupe in the frontend is by id, so the same edge reached from either
    end has to come back with the same id or it renders twice."""
    from_caller = asyncio.run(graph_api.expand_node("proj/src/app.py::main"))
    from_callee = asyncio.run(graph_api.expand_node("proj/src/util.py::helper"))
    shared = {edge.id for edge in from_caller.edges} & {
        edge.id for edge in from_callee.edges
    }
    assert len(shared) == 1

    twice = asyncio.run(graph_api.expand_node("proj/src"))
    assert len({node.id for node in twice.nodes}) == len(twice.nodes)
    assert len({edge.id for edge in twice.edges}) == len(twice.edges)


def test_expanding_something_that_is_not_there_is_a_404_not_an_empty_graph(rag):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        asyncio.run(graph_api.expand_node("proj/nope"))
    assert raised.value.status_code == 404
