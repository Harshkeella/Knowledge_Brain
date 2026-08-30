"""Ground truth from the filesystem, compared to what actually landed.

    python -m scripts.verify_depth <folder> [name]      # re-ingest and check
    python -m scripts.verify_depth --check <name>       # check what is stored

This is the check the depth fix is signed off against, kept runnable so a
future regression is one command away instead of a re-derivation. It walks the
tree with `os.walk` and the ingest's own ignore rules, then asks the graph the
same three questions -- how many folders, how many files, how deep -- plus the
zero-gap chain check: every folder at depth N hangs off exactly one folder at
depth N-1.
"""

import argparse
import asyncio
import os
import sys

from app.services import folder_ingest, manifest
from app.services import graph_schema as gs
from app.services.lightrag_engine import get_rag, shutdown_rag


def ground_truth(root: str, name: str) -> dict:
    tree = folder_ingest.scan(root, name)
    return {
        "folders": len(tree["folders"]),
        "files": len(tree["files"]),
        "max_depth": max((f["depth"] for f in tree["folders"]), default=0),
    }


async def in_graph(rag, name: str) -> dict:
    graph = rag.chunk_entity_relation_graph
    prefix = f"{name}/"
    mine = [
        node
        for node in await graph.get_all_nodes()
        if node["id"] == name or str(node["id"]).startswith(prefix)
    ]
    folders = [n for n in mine if n.get("entity_type") == gs.FOLDER]
    # Leaves only. Code symbols share the id prefix and are not files.
    leaf_labels = {gs.FILE, gs.CODE_FILE, gs.IMAGE, gs.VIDEO}
    files = [n for n in mine if n.get("entity_type") in leaf_labels]

    # The zero-gap chain check. One parent, at exactly one level up.
    by_id = {n["id"]: int(n.get("depth") or 0) for n in folders}
    orphans = []
    for node_id, depth in by_id.items():
        if depth == 0:
            continue
        parents = []
        # networkx yields every incident edge as (queried_node, neighbour), so
        # the tuple order says nothing about direction -- the edge's own
        # rel_from/rel_to does. Reading the tuple is how you conclude a healthy
        # tree has no parents at all.
        for pair in await graph.get_node_edges(node_id) or []:
            edge = await graph.get_edge(*pair) or {}
            if edge.get("keywords") != gs.CONTAINS_FOLDER:
                continue
            if (edge.get("rel_to") or pair[1]) == node_id:
                parents.append(edge.get("rel_from") or pair[0])
        if len(parents) != 1 or by_id.get(parents[0]) != depth - 1:
            orphans.append((node_id, parents))

    source = await graph.get_node(f"source:{name}")
    return {
        "folders": len(folders),
        "files": len(files),
        "max_depth": max(by_id.values(), default=0),
        "orphans": orphans,
        "source": {
            key: (source or {}).get(key)
            for key in ("status", "total_folders", "total_files", "max_depth_reached",
                        "documents_indexed")
        },
    }


def report(name: str, truth: dict | None, stored: dict) -> bool:
    print(f"\n== {name} ==")
    ok = True
    if truth:
        for key in ("folders", "files", "max_depth"):
            match = truth[key] == stored[key]
            ok &= match
            print(
                f"  {key:<10} filesystem={truth[key]:<6} graph={stored[key]:<6} "
                f"{'OK' if match else 'MISMATCH'}"
            )
    else:
        for key in ("folders", "files", "max_depth"):
            print(f"  {key:<10} graph={stored[key]}")
    print(f"  chain gaps: {len(stored['orphans'])} {'OK' if not stored['orphans'] else stored['orphans'][:5]}")
    ok &= not stored["orphans"]
    print(f"  Source node: {stored['source']}")
    return ok


async def main(argv) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="folder path, or source name with --check")
    parser.add_argument("name", nargs="?", default=None)
    parser.add_argument("--check", action="store_true", help="do not re-ingest")
    parser.add_argument("--index-documents", action="store_true")
    args = parser.parse_args(argv)

    await manifest.init_db()
    rag = await get_rag()
    try:
        if args.check:
            return 0 if report(args.target, None, await in_graph(rag, args.target)) else 1

        root = os.path.abspath(args.target)
        name = args.name or os.path.basename(root)
        truth = ground_truth(root, name)
        await folder_ingest.ingest_folder(
            root, name=name, index_documents=args.index_documents
        )
        await folder_ingest.wait_for_documents()
        return 0 if report(name, truth, await in_graph(rag, name)) else 1
    finally:
        await shutdown_rag()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
