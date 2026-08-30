"""Give every inventory row a Source node, so the hop view can see it.

    python -m scripts.backfill_sources            # list what is missing
    python -m scripts.backfill_sources --apply    # write the missing nodes

The Graph Explorer's landing state is `count(Source)` nodes and nothing else,
so anything ingested before the supernode existed has no way in -- its
entities are still in the graph, but there is no node to double-click. This
walks the manifest and registers the ones that were missed.

Non-destructive and idempotent: `source_graph.register` upserts by node id and
finds the ingestion's own output by exact `file_path` match, which is the same
thing a fresh upload does. Folder sources are skipped -- theirs is written by
the walker with the tree's counts on it, and re-registering would replace those
with a `file_path` scan.
"""

import argparse
import asyncio
import sys

from app.services import manifest, source_graph
from app.services.lightrag_engine import get_rag, shutdown_rag


async def main(apply: bool) -> int:
    await manifest.init_db()
    rag = await get_rag()
    try:
        graph = rag.chunk_entity_relation_graph
        missing = []
        for row in await manifest.list_documents():
            if row["source_type"] == "folder":
                continue
            if await graph.get_node(source_graph.source_node(row["file_name"])) is None:
                missing.append(row)

        if not missing:
            print("Every document already has a Source node.")
            return 0

        for row in missing:
            print(f"{'writing' if apply else 'missing'}: {row['file_name']}")
            if apply:
                await source_graph.register(rag, row)
        if not apply:
            print(f"\n{len(missing)} missing. Re-run with --apply to write them.")
        return 0
    finally:
        await shutdown_rag()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args(sys.argv[1:]).apply)))
