"""The tree in the graph has to be the tree on disk -- no invented nodes, no
silently swallowed subdirectories, and nothing from the directories everyone
means to exclude."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from app.services import folder_ingest, manifest
from app.services import graph_schema as gs
from app.services.ingestion import IngestionError
from app.services.test_source_graph import FakeRag


def make_deep_tree(root, depth: int = 20) -> str:
    """A tree `depth` levels deep, with real content and real noise at EVERY
    level -- the harness the depth fix is verified against. Re-runnable:

        python -m app.services.test_folder_ingest ./deep-tree 20

    Every level gets a .py, a .ts, a .png and a binary blob, so a level that
    goes missing is missing from four different counts, and a `node_modules`
    beside it, so exclusion is proven at depth N and not just at the root.
    """
    root = Path(root) / "deeptree"
    here = root
    for level in range(depth):
        here.mkdir(parents=True, exist_ok=True)
        (here / f"mod{level}.py").write_text(
            f"def level_{level}():\n    return {level}\n", encoding="utf-8"
        )
        (here / f"mod{level}.ts").write_text(
            f"export const level{level} = {level};\n", encoding="utf-8"
        )
        (here / f"pic{level}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        (here / f"blob{level}.bin").write_bytes(bytes(32))
        # Noise at every level, not just the top -- a prune that only fires on
        # the first directory it sees would still pass a root-only test.
        noise = here / "node_modules" / "pkg"
        noise.mkdir(parents=True, exist_ok=True)
        (noise / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
        here = here / f"level{level + 1}"
    return str(root)


def _tree(root) -> str:
    (root / "proj").mkdir()
    proj = root / "proj"
    (proj / "README.md").write_text("# hello", encoding="utf-8")
    (proj / "debug.log").write_text("noise", encoding="utf-8")
    (proj / ".kbignore").write_text("# local rules\n*.log\nscratch/\n", encoding="utf-8")

    (proj / "src").mkdir()
    (proj / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (proj / "src" / "util.js").write_text("export const a = 1;\n", encoding="utf-8")
    (proj / "src" / "deep").mkdir()
    (proj / "src" / "deep" / "notes.txt").write_text("deep", encoding="utf-8")

    (proj / "assets").mkdir()
    (proj / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (proj / "assets" / "clip.mp4").write_bytes(b"\x00" * 16)

    # Both must be pruned: one by the built-in list, one by .kbignore.
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "junk.js").write_text("junk", encoding="utf-8")
    (proj / "scratch").mkdir()
    (proj / "scratch" / "tmp.py").write_text("tmp", encoding="utf-8")
    return str(proj)


@pytest.fixture
def ingested(tmp_path, monkeypatch):
    path = _tree(tmp_path)
    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path / "storage"))
    asyncio.run(manifest.init_db())

    rag = FakeRag()

    async def fake_get_rag():
        return rag

    monkeypatch.setattr(folder_ingest, "get_rag", fake_get_rag)
    # Structure only here; routing documents through the real ingestors is the
    # ingestors' own tested behaviour, not this walker's.
    result = asyncio.run(
        folder_ingest.ingest_folder(path, name="proj", index_documents=False)
    )
    return rag, result, path


def test_ignored_trees_never_appear(ingested):
    rag, result, _ = ingested
    ids = set(rag.chunk_entity_relation_graph.nodes)
    assert not any("node_modules" in node for node in ids), "built-in ignore"
    assert not any("scratch" in node for node in ids), ".kbignore directory"
    assert not any(node.endswith(".log") for node in ids), ".kbignore glob"
    assert not any(node.endswith(".kbignore") for node in ids)
    assert result["folders"] == 4, "proj, src, src/deep, assets"


def test_every_leaf_gets_the_label_its_extension_earns(ingested):
    rag, _, _ = ingested
    nodes = rag.chunk_entity_relation_graph.nodes
    assert nodes["proj/src/main.py"]["entity_type"] == gs.CODE_FILE
    assert nodes["proj/src/main.py"]["language"] == "python"
    assert nodes["proj/src/util.js"]["language"] == "javascript"
    assert nodes["proj/assets/logo.png"]["entity_type"] == gs.IMAGE
    assert nodes["proj/assets/clip.mp4"]["entity_type"] == gs.VIDEO
    assert nodes["proj/README.md"]["entity_type"] == gs.FILE
    assert nodes["proj/src"]["entity_type"] == gs.FOLDER
    assert nodes["proj/src/deep"]["depth"] == 2


def test_the_tree_is_wired_exactly_like_the_filesystem(ingested):
    rag, _, _ = ingested
    edges = rag.chunk_entity_relation_graph.edges
    assert edges[("proj", "proj/src")]["keywords"] == gs.CONTAINS_FOLDER
    assert edges[("proj/src", "proj/src/deep")]["keywords"] == gs.CONTAINS_FOLDER
    assert edges[("proj/src", "proj/src/main.py")]["keywords"] == gs.CONTAINS_FILE
    assert edges[("proj/assets", "proj/assets/logo.png")]["keywords"] == gs.CONTAINS_FILE
    # A file is never wired to the root just because the root is convenient.
    assert ("proj", "proj/src/main.py") not in edges


def test_one_supernode_pointing_at_the_root_only(ingested):
    rag, result, path = ingested
    node = rag.chunk_entity_relation_graph.nodes["source:proj"]
    assert node["entity_type"] == gs.SOURCE
    assert node["source_type"] == "folder"
    assert node["code_files"] == 2 and node["images"] == 1 and node["videos"] == 1
    assert node["origin_path"] == os.path.abspath(path)

    roots = [
        target
        for (source, target) in rag.chunk_entity_relation_graph.edges
        if source == "source:proj"
    ]
    assert roots == ["proj"], "everything else hangs off the root folder"


def test_reingest_reuses_the_inventory_row(ingested, monkeypatch):
    rag, result, path = ingested
    again = asyncio.run(
        folder_ingest.ingest_folder(path, name="proj", index_documents=False)
    )
    assert again["doc_id"] == result["doc_id"]
    assert len(asyncio.run(manifest.list_documents())) == 1


def test_confinement_rejects_paths_outside_the_allowed_root(tmp_path, monkeypatch):
    path = _tree(tmp_path)
    monkeypatch.setattr(
        folder_ingest._settings, "folder_ingest_root", str(tmp_path / "elsewhere")
    )
    with pytest.raises(IngestionError, match="confined"):
        asyncio.run(folder_ingest.ingest_folder(path, name="proj"))


def test_removal_takes_the_whole_subtree_and_nothing_else(ingested):
    rag, _, _ = ingested
    rag.chunk_entity_relation_graph.nodes["ACME CORP"] = {"entity_type": "organization"}
    removed = asyncio.run(folder_ingest.remove(rag, "proj"))
    assert removed >= 10
    assert list(rag.chunk_entity_relation_graph.nodes) == ["ACME CORP"]


# ---------------------------------------------------------------------------
# Depth. The walk has to reach the bottom of the tree, whatever the bottom is.
# ---------------------------------------------------------------------------

DEEP = 20


@pytest.fixture
def deep(tmp_path, monkeypatch):
    path = make_deep_tree(tmp_path, DEEP)
    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path / "storage"))
    asyncio.run(manifest.init_db())

    rag = FakeRag()

    async def fake_get_rag():
        return rag

    monkeypatch.setattr(folder_ingest, "get_rag", fake_get_rag)
    result = asyncio.run(
        folder_ingest.ingest_folder(path, name="deep", index_documents=False)
    )
    return rag, result


def test_the_walk_reaches_the_bottom_of_the_tree(deep):
    rag, result = deep
    depths = [
        node["depth"]
        for node in rag.chunk_entity_relation_graph.nodes.values()
        if node.get("entity_type") == gs.FOLDER
    ]
    assert max(depths) == DEEP - 1, "every level, not the first few"
    assert sorted(depths) == list(range(DEEP)), "one folder per level, no gaps"
    assert result["max_depth_reached"] == DEEP - 1
    assert result["total_folders"] == DEEP
    # Four files per level, and nothing from node_modules.
    assert result["total_files"] == DEEP * 4


def test_node_modules_is_pruned_at_every_level_not_just_the_root(deep):
    rag, _ = deep
    ids = list(rag.chunk_entity_relation_graph.nodes)
    assert not any("node_modules" in node for node in ids)
    assert not any(node.endswith("index.js") for node in ids)


def test_every_folder_has_exactly_one_parent(deep):
    """The zero-gap chain check: a folder at depth N hangs off exactly one
    folder at depth N-1. A tree with a gap renders as an orphan cluster."""
    rag, _ = deep
    nodes = rag.chunk_entity_relation_graph.nodes
    folders = {
        node_id: data
        for node_id, data in nodes.items()
        if data.get("entity_type") == gs.FOLDER
    }
    parents: dict[str, list[str]] = {node_id: [] for node_id in folders}
    for (source, target), edge in rag.chunk_entity_relation_graph.edges.items():
        if edge["keywords"] == gs.CONTAINS_FOLDER:
            parents[target].append(source)

    orphans = {
        node_id: len(found)
        for node_id, found in parents.items()
        if len(found) != (0 if folders[node_id]["depth"] == 0 else 1)
    }
    assert orphans == {}, f"folders with the wrong parent count: {orphans}"
    for node_id, found in parents.items():
        if found:
            assert folders[found[0]]["depth"] == folders[node_id]["depth"] - 1


def test_every_file_hangs_off_the_folder_it_is_actually_in(deep):
    rag, _ = deep
    nodes = rag.chunk_entity_relation_graph.nodes
    for (source, target), edge in rag.chunk_entity_relation_graph.edges.items():
        if edge["keywords"] != gs.CONTAINS_FILE:
            continue
        assert target.rsplit("/", 1)[0] == source
        assert nodes[source]["entity_type"] == gs.FOLDER


def test_an_explicit_depth_limit_is_the_only_thing_that_clips_the_walk(
    tmp_path, monkeypatch
):
    """The default is a sentinel far past any real tree; a small value is
    opt-in. This is the ceiling that did NOT exist and now does, on purpose."""
    path = make_deep_tree(tmp_path, DEEP)
    monkeypatch.setattr(folder_ingest._settings, "folder_max_depth", 3)
    clipped = folder_ingest.scan(path, "deep")
    assert max(entry["depth"] for entry in clipped["folders"]) == 3

    monkeypatch.setattr(folder_ingest._settings, "folder_max_depth", 1000)
    full = folder_ingest.scan(path, "deep")
    assert max(entry["depth"] for entry in full["folders"]) == DEEP - 1


def test_documents_are_indexed_after_the_tree_is_already_durable(tmp_path, monkeypatch):
    """The regression that lost 30 of 33 file nodes: the document pass used to
    run INSIDE the tree-write loop, so anything that killed the request (a
    timeout cancelling the task) took every unwritten leaf with it."""
    path = _tree(tmp_path)
    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path / "storage"))
    asyncio.run(manifest.init_db())
    rag = FakeRag()

    async def fake_get_rag():
        return rag

    seen_tree_size: list[int] = []

    async def fake_ingest_file_bytes(data, file_name):
        # By the time any document is touched the whole tree must already be
        # committed -- that is the entire point of the reordering.
        seen_tree_size.append(
            len([n for n in rag.chunk_entity_relation_graph.nodes if n.startswith("proj")])
        )
        return {"doc_id": f"doc-{file_name}"}

    monkeypatch.setattr(folder_ingest, "get_rag", fake_get_rag)
    monkeypatch.setattr(folder_ingest, "ingest_file_bytes", fake_ingest_file_bytes)

    async def run():
        result = await folder_ingest.ingest_folder(path, name="proj")
        await folder_ingest.wait_for_documents()
        return result

    result = asyncio.run(run())
    assert result["status"] == folder_ingest.PROCESSING
    assert result["documents_pending"] == 2, "README.md and src/deep/notes.txt"

    tree_size = len(
        [n for n in rag.chunk_entity_relation_graph.nodes if n.startswith("proj")]
    )
    assert seen_tree_size == [tree_size, tree_size], "tree written before any document"

    source = rag.chunk_entity_relation_graph.nodes["source:proj"]
    assert source["status"] == folder_ingest.COMPLETED
    assert source["documents_indexed"] == 2
    leaf = rag.chunk_entity_relation_graph.nodes["proj/README.md"]
    assert leaf["doc_id"] == "doc-README.md"
    assert leaf["entity_type"] == gs.FILE, "patching doc_id must not restate the label"


def test_a_failing_document_never_costs_you_the_tree(tmp_path, monkeypatch):
    path = _tree(tmp_path)
    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path / "storage"))
    asyncio.run(manifest.init_db())
    rag = FakeRag()

    async def fake_get_rag():
        return rag

    async def boom(data, file_name):
        raise RuntimeError("extraction exploded")

    monkeypatch.setattr(folder_ingest, "get_rag", fake_get_rag)
    monkeypatch.setattr(folder_ingest, "ingest_file_bytes", boom)

    async def run():
        result = await folder_ingest.ingest_folder(path, name="proj")
        await folder_ingest.wait_for_documents()
        return result

    result = asyncio.run(run())
    assert result["folders"] == 4 and result["files"] == 6
    source = rag.chunk_entity_relation_graph.nodes["source:proj"]
    # The documents failed; the folder did not.
    assert source["status"] == folder_ingest.COMPLETED
    assert source["documents_indexed"] == 0
    assert source["document_errors"] == 2
    assert "proj/src/main.py" in rag.chunk_entity_relation_graph.nodes


def test_the_source_node_reports_the_shape_of_the_tree(ingested):
    rag, result, _ = ingested
    source = rag.chunk_entity_relation_graph.nodes["source:proj"]
    assert source["total_folders"] == 4
    assert source["total_files"] == result["files"]
    assert source["max_depth_reached"] == 2, "proj/src/deep"
    assert source["status"] == folder_ingest.COMPLETED, "no documents to index"


if __name__ == "__main__":
    # The generator, runnable on its own so a real ingest can be pointed at a
    # provably deep tree without pytest in the loop.
    where = sys.argv[1] if len(sys.argv) > 1 else "."
    levels = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    print(make_deep_tree(where, levels))
