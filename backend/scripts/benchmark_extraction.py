"""Time a book-sized ingest end to end, into a throwaway storage dir.

    python scripts/benchmark_extraction.py [path/to/doc.txt] [--words 90000]

With no path it synthesizes ~90,000 words (a 200-page book) of varied prose --
varied because identical chunks would hit LightRAG's extraction cache and
report a fictional number. Compare backends by re-running with
EXTRACTION_BACKEND=llm.
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TEMP_STORAGE = tempfile.mkdtemp(prefix="noderels-bench-")
os.environ["STORAGE_DIR"] = _TEMP_STORAGE

from app.core.config import get_settings  # noqa: E402
from app.services.lightrag_engine import get_rag, shutdown_rag  # noqa: E402

_TEMPLATE = (
    "In {year}, {person} joined {org} as a research lead in {city}. "
    "The team shipped {product}, a system built on {tech} that {org} "
    "announced at the {year} developer conference. {person} said {product} "
    "would compete directly with rival offerings from {rival}. "
)
_PEOPLE = ["Ada Byrne", "Kenji Mori", "Lena Ortiz", "Samuel Adeyemi", "Priya Raman"]
_ORGS = ["Northwind Labs", "Acme Robotics", "Helios Systems", "Vector Foundry"]
_CITIES = ["Lisbon", "Osaka", "Nairobi", "Toronto", "Bergen"]
_PRODUCTS = ["Lantern", "Quarry", "Beacon", "Tidewater", "Foxglove"]
_TECHS = ["graph databases", "vector search", "reinforcement learning", "FPGAs"]


def synthesize(words: int) -> str:
    out, count, i = [], 0, 0
    while count < words:
        text = _TEMPLATE.format(
            year=1990 + i % 35,
            person=_PEOPLE[i % len(_PEOPLE)],
            org=_ORGS[i % len(_ORGS)],
            city=_CITIES[i % len(_CITIES)],
            product=_PRODUCTS[i % len(_PRODUCTS)],
            tech=_TECHS[i % len(_TECHS)],
            rival=_ORGS[(i + 1) % len(_ORGS)],
        )
        out.append(f"Section {i}. {text}")
        count += len(text.split()) + 2
        i += 1
    return "\n\n".join(out)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="document to ingest (default: synthesized)")
    parser.add_argument("--words", type=int, default=90_000)
    args = parser.parse_args()

    text = Path(args.path).read_text(encoding="utf-8") if args.path else synthesize(args.words)
    settings = get_settings()
    word_count = len(text.split())

    print(f"backend={settings.extraction_backend} words={word_count:,}")
    if settings.extraction_backend == "gliner":
        from app.services.gliner_extract import warmup

        t = time.perf_counter()
        await asyncio.to_thread(warmup)
        print(f"model load: {time.perf_counter() - t:.1f}s (paid once at boot, excluded below)")

    rag = await get_rag()
    start = time.perf_counter()
    await rag.ainsert(input=[text], ids=["bench-doc"], file_paths=["benchmark.txt"])
    elapsed = time.perf_counter() - start

    nodes = len(await rag.chunk_entity_relation_graph.get_all_labels())
    print(
        f"chunk -> graph-queryable: {elapsed:.1f}s "
        f"({word_count / elapsed:,.0f} words/s, {nodes:,} entities)"
    )

    await shutdown_rag()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(_TEMP_STORAGE, ignore_errors=True)
