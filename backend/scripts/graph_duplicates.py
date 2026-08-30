"""Report entities the graph has split across several nodes.

    python -m scripts.graph_duplicates            # every split, grouped
    python -m scripts.graph_duplicates --type person

Read-only, always. There is no `--apply` here on purpose: the graph is derived
data, so the way to collapse these groups is to re-ingest the documents with
the fold in `graph_schema.canonical_name` fixed, not to perform surgery on the
nodes and hope the edges, provenance and vector records all get repointed. Use
this to see how widespread a split is, and to confirm a re-ingest cleared it.

Two kinds of split are reported separately because only one of them is safe to
act on automatically:

  RESOLVED   several node ids that `canonical_name` folds to one name today --
             "LIONEL_MESSI" and "Lionel Messi", say. These were split by a bug
             that is now fixed; a re-ingest merges them with no judgement call.

  AMBIGUOUS  a short name that is a prefix or suffix of several longer ones --
             "RONALDO" against "CRISTIANO RONALDO" and "RONALDO JR". Which one
             a mention meant is a question about the source text, not about the
             strings, so these are listed for a human and never merged.
"""

import argparse
import collections
import glob
import os
import sys

import networkx as nx

from app.core.config import get_settings
from app.services import graph_schema


_MAX_CANDIDATES = 4


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(graph_schema.canonical_name(name).split())


def group_splits(
    names: list[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(resolved, ambiguous) for one entity type.

    `resolved` maps a canonical name to the >1 node ids that fold onto it.
    `ambiguous` maps a short name to the longer names it could be part of --
    only prefixes and suffixes count, so "MICHAEL JORDAN" is not treated as a
    form of "MICHAEL I JORDAN" (a different person, and the tokens are not
    contiguous), while "RONALDO JR" is a candidate form of "CRISTIANO RONALDO
    JR" (the same person, named in full).
    """
    folded: dict[str, list[str]] = collections.defaultdict(list)
    for name in names:
        canonical = graph_schema.canonical_name(name)
        if canonical:
            folded[canonical].append(name)

    resolved = {k: sorted(v) for k, v in folded.items() if len(v) > 1}

    distinct = {_tokens(name) for name in names if _tokens(name)}
    ambiguous: dict[str, list[str]] = {}
    for short in distinct:
        longer = sorted(
            " ".join(other)
            for other in distinct
            if len(other) > len(short)
            and (other[: len(short)] == short or other[-len(short) :] == short)
        )
        # A name that is the head of dozens of longer ones is not an ambiguous
        # person, it is a real name buried under headline nodes that a URL
        # built ("CRISTIANO RONALDO" against 130 article titles). Those are the
        # bug `_URL_RE` now prevents, so listing them here would bury the few
        # genuine cases -- report only what a human could plausibly adjudicate.
        if 0 < len(longer) <= _MAX_CANDIDATES:
            ambiguous[" ".join(short)] = longer
    return resolved, ambiguous


def main(entity_type: str | None) -> int:
    # Read the graphml rather than going through get_rag(): the engine also
    # opens Qdrant, whose embedded mode allows a single process, so asking it
    # for a read-only report fails whenever the API server is up -- which is
    # exactly when you want to run this.
    working_dir = get_settings().kb_working_dir
    found = glob.glob(os.path.join(working_dir, "graph_*.graphml"))
    if not found:
        print(f"No graph in {working_dir} -- nothing ingested yet.")
        return 0
    graph = nx.read_graphml(found[0])

    by_type: dict[str, list[str]] = collections.defaultdict(list)
    for name, node in graph.nodes(data=True):
        by_type[node.get("entity_type") or graph_schema.UNRESOLVED].append(name)

    total_resolved = total_ambiguous = 0
    for label in sorted(by_type):
        if entity_type and label != graph_schema.canonical_label(entity_type):
            continue
        resolved, ambiguous = group_splits(by_type[label])
        total_resolved += sum(len(v) - 1 for v in resolved.values())
        total_ambiguous += len(ambiguous)
        if not resolved and not ambiguous:
            continue

        print(f"\n=== {label} ({len(by_type[label])} nodes) ===")
        for canonical, nodes in sorted(resolved.items()):
            print(f"  RESOLVED  {canonical!r} <- {nodes}")
        # One candidate is a likely alias ("CASILLAS" -> "IKER CASILLAS");
        # several is a genuine question about the text ("RONALDO" -> the father
        # or the son). Neither is merged here, but only the first is a
        # one-glance confirmation, so they are not worth the same line.
        for short, longer in sorted(ambiguous.items()):
            if len(longer) == 1:
                print(f"  LIKELY    {short!r} -> {longer[0]!r}")
        for short, longer in sorted(ambiguous.items()):
            if len(longer) > 1:
                print(f"  AMBIGUOUS {short!r} could be any of {longer}")

    print(
        f"\n{total_resolved} node(s) collapse on re-ingest; "
        f"{total_ambiguous} name(s) need a human."
    )
    return 0


if __name__ == "__main__":
    # Entity names come from arbitrary source documents, so a mojibake byte in
    # one of them must not take the report down on a cp1252 console.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", dest="entity_type", help="only this entity type")
    sys.exit(main(parser.parse_args().entity_type))
