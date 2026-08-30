"""Re-scan every folder source, so old ingests get the current graph.

    python -m scripts.reingest_folders            # list what would be redone
    python -m scripts.reingest_folders --apply    # actually re-scan

Manual, never run at startup, and non-destructive: `ingest_folder` is
idempotent on the same path + name, so it rewrites the same nodes in place and
keeps the inventory row's `doc_id`. Documents indexed from inside the folder
are already deduped by content hash and are not re-processed.

Why this and not a `calls_unresolved` -> ExternalSymbol converter: those
strings are names with no receiver, no import context and no line numbers. A
converter could not tell `json.dumps` from `len`, so it would create exactly
the builtin hubs (`len`, `append`, `str`) that make a call graph unreadable,
and it could not fill in `module_guess`, `call_site_line` or `confidence`.
Re-parsing the files recovers all of it, and the parser is already written.

It is also the repair path for folders ingested before direct graph writes were
flushed -- those trees reached disk only partially, and their code symbols
(written last) not at all.
"""

import argparse
import asyncio
import sys

from app.services import folder_ingest, manifest
from app.services.lightrag_engine import get_rag, shutdown_rag


async def main(apply: bool) -> int:
    await manifest.init_db()
    folders = [
        row for row in await manifest.list_documents() if row["source_type"] == "folder"
    ]
    if not folders:
        print("No folder sources in the inventory.")
        return 0

    rag = await get_rag()
    graph = rag.chunk_entity_relation_graph
    for row in folders:
        name = row["file_name"]
        node = await graph.get_node(f"source:{name}")
        path = (node or {}).get("origin_path")
        present = len(
            [
                n
                for n in await graph.get_all_nodes()
                if n["id"] == name or str(n["id"]).startswith(f"{name}/")
            ]
        )
        claimed = int((node or {}).get("file_count") or 0) + int(
            (node or {}).get("folder_count") or 0
        )
        print(f"{name}: {present} node(s) on disk, {claimed} expected  <- {path}")

        if not apply:
            continue
        if not path:
            print("  skipped: no origin_path recorded; re-add it from the UI")
            continue
        try:
            result = await folder_ingest.ingest_folder(path, name=name)
            print(
                f"  re-scanned: {result['files']} file(s), {result['functions']} "
                f"function(s), {result['calls']} call edge(s), "
                f"{result['external_symbols']} external symbol(s)"
            )
        except Exception as e:
            # One unreadable folder must not abandon the rest.
            print(f"  FAILED: {e}")

    await shutdown_rag()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="re-scan (default is a dry run)"
    )
    sys.exit(asyncio.run(main(parser.parse_args().apply)))
