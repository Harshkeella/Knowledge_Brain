"""The property-graph model: Neo4j's shape, none of Neo4j.

A property graph is nodes that carry a *label* plus properties and edges that
carry a *type* plus properties, with both vocabularies closed so the graph
stays queryable. That is a modelling discipline, not a database, so it is
enforced here -- at the two functions everything writes through -- while the
graph itself stays in the local store LightRAG already persists.

Labels ride on `entity_type` and relationship types on `keywords`, which are
the properties the existing graph API and Graph Explorer already read. Nothing
downstream needs a new field.

Ceiling: the underlying store keeps one edge per node pair, so two different
relationship types between the same two nodes would collide. The tabular
projection only ever connects distinct pairs. Move to a multigraph store
(Kuzu, Memgraph) if that stops being true.
"""

import re
import time

from lightrag.utils import compute_mdhash_id

from app.core.config import get_settings

_settings = get_settings()

# Things found in prose by the extractor -- the ENTITY_LABELS ontology, in the
# graph's canonical casing.
UNRESOLVED = "UNKNOWN"

_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
# `_` and `-` are separators, not part of the name. A caption ("LIONEL_MESSI"),
# a slug ("CRISTIANO-RONALDO") and prose ("Lionel Messi") are one person, and
# without this fold they are three nodes -- 35 such groups in a single ingest.
# Folded here rather than in a resolver because this is the one function every
# write path already routes through, so the split never reaches the graph.
_SEPARATOR_RE = re.compile(r"[_\-‐-―]+")
_POSSESSIVE_RE = re.compile(r"['’]s$", re.IGNORECASE)
_EDGE_PUNCTUATION = " \t\n\r.,;:!?'\"()[]{}<>|/\\"


def canonical_label(raw: str | None) -> str:
    """One spelling for a label, everywhere.

    LightRAG normalizes every extracted entity type to `.replace(" ","").lower()`
    before it reaches the graph, so anything written past it (the tabular
    projection) has to use the same form or the legend splits into `person` and
    `Person`, `column` and `Column`. Lower case is not a preference here, it is
    the only form both write paths can agree on.
    """
    cleaned = re.sub(r"[^\w\s]+", " ", str(raw or ""))
    cleaned = re.sub(r"[\s_]+", "", cleaned).lower()
    if not cleaned or cleaned == UNRESOLVED.lower():
        return UNRESOLVED
    return cleaned


def canonical_name(raw: str | None) -> str:
    """One node per thing.

    LightRAG merges entities by exact name string, so "Microsoft", "microsoft",
    "the Microsoft" and "Microsoft's" are four nodes for one company unless
    they are folded before extraction hands them over. Upper case is LightRAG's
    own naming convention (its default extraction prompt asks for it) and is
    the only fold that is deterministic without a cross-chunk registry.
    """
    name = _SEPARATOR_RE.sub(" ", str(raw or ""))
    name = re.sub(r"\s+", " ", name).strip(_EDGE_PUNCTUATION)
    name = _POSSESSIVE_RE.sub("", _ARTICLE_RE.sub("", name)).strip(_EDGE_PUNCTUATION)
    return name.upper()


# Structure extracted from spreadsheets, written deterministically.
WORKBOOK = canonical_label("Workbook")
WORKSHEET = canonical_label("Worksheet")
COLUMN = canonical_label("Column")
TABULAR_LABELS = frozenset({WORKBOOK, WORKSHEET, COLUMN})

# One node per ingestion event -- the addressable root of everything an upload
# produced. `entity_type` carries the label here as everywhere else, so the
# Graph Explorer picks these up with no frontend change.
SOURCE = canonical_label("Source")

# Filesystem structure and code symbols from a folder ingest. Same deal: no new
# store, no new property, just labels the closed ontology now admits.
FOLDER = canonical_label("Folder")
FILE = canonical_label("File")
CODE_FILE = canonical_label("CodeFile")
CLASS = canonical_label("Class")
FUNCTION = canonical_label("Function")
METHOD = canonical_label("Method")
IMAGE = canonical_label("Image")
VIDEO = canonical_label("Video")
# A call target that is not in the scanned tree -- a library or a builtin.
# One node per unique name, so an unresolved call is still a visible edge
# instead of a string buried on the caller.
EXTERNAL_SYMBOL = canonical_label("ExternalSymbol")
# Ceiling: the graph is single-label, so a code file is `codefile` and not both
# `file` and `codefile`. Query the one label, not a hierarchy.
STRUCTURE_LABELS = frozenset(
    {FOLDER, FILE, CODE_FILE, CLASS, FUNCTION, METHOD, IMAGE, VIDEO, EXTERNAL_SYMBOL}
)
# What the "code only" view keeps. Everything else is document/tabular.
CODE_LABELS = frozenset(
    {FOLDER, CODE_FILE, CLASS, FUNCTION, METHOD, EXTERNAL_SYMBOL}
)


DOCUMENT_LABELS = frozenset(canonical_label(label) for label in _settings.entity_labels)
NODE_LABELS = (
    DOCUMENT_LABELS | TABULAR_LABELS | STRUCTURE_LABELS | {SOURCE, UNRESOLVED}
)

# Relationship vocabulary, closed like the labels are. Co-occurrence edges from
# the text extractor are RELATED_TO by construction; their evidence sentence is
# the description. The rest are written deterministically and carry their type
# explicitly. Free-text edge "types" are what turn a legend into noise, so this
# set is the only thing `keywords` is ever allowed to hold.
RELATED_TO = "RELATED_TO"
HAS_SHEET = "HAS_SHEET"
HAS_COLUMN = "HAS_COLUMN"
DERIVED_FROM = "DERIVED_FROM"
HAS_VALUE = "HAS_VALUE"
# Provenance: Source -> whatever that ingestion put in the graph.
HAS_ROOT = "HAS_ROOT"
# Filesystem and code structure.
CONTAINS_FOLDER = "CONTAINS_FOLDER"
CONTAINS_FILE = "CONTAINS_FILE"
DEFINES = "DEFINES"
DEFINES_METHOD = "DEFINES_METHOD"
CALLS = "CALLS"
IMPORTS = "IMPORTS"
INHERITS = "INHERITS"
# INHERITS is class -> class. IMPLEMENTS is class -> interface, and is only
# ever emitted for languages that actually have interfaces (TS, Java); Python
# has no such concept, so a Python base class is always INHERITS.
IMPLEMENTS = "IMPLEMENTS"
REL_TYPES = frozenset(
    {
        RELATED_TO,
        HAS_SHEET,
        HAS_COLUMN,
        DERIVED_FROM,
        HAS_VALUE,
        HAS_ROOT,
        CONTAINS_FOLDER,
        CONTAINS_FILE,
        DEFINES,
        DEFINES_METHOD,
        CALLS,
        IMPORTS,
        INHERITS,
        IMPLEMENTS,
    }
)

# How an edge should be read, and therefore drawn. Derived from the
# relationship type rather than stored on the edge: it is a pure function of
# `keywords`, so writing it would be a denormalised copy -- and one that only
# OUR edges could carry, since LightRAG writes its own RELATED_TO edges
# directly. Derived, every edge has it, old ones included, with no backfill.
STRUCTURAL, BEHAVIORAL, SEMANTIC = "structural", "behavioral", "semantic"
_EDGE_CATEGORY = {
    HAS_ROOT: STRUCTURAL,
    CONTAINS_FOLDER: STRUCTURAL,
    CONTAINS_FILE: STRUCTURAL,
    DEFINES: STRUCTURAL,
    DEFINES_METHOD: STRUCTURAL,
    HAS_SHEET: STRUCTURAL,
    HAS_COLUMN: STRUCTURAL,
    CALLS: BEHAVIORAL,
    IMPORTS: BEHAVIORAL,
    INHERITS: BEHAVIORAL,
    IMPLEMENTS: BEHAVIORAL,
    DERIVED_FROM: BEHAVIORAL,
}


def edge_category(rel_type: str | None) -> str:
    """`structural` | `behavioral` | `semantic`. Anything the extractor wrote
    (RELATED_TO, or a free-text keyword from an older ingest) is semantic."""
    return _EDGE_CATEGORY.get(str(rel_type or ""), SEMANTIC)


async def flush(rag) -> None:
    """Commit direct graph writes to disk.

    LightRAG's storage says it plainly: "Callers outside the pipeline must
    persist explicitly." `upsert_node`/`upsert_edge` only mutate memory --
    inside `ainsert()` the pipeline's own `_insert_done()` commits at the end
    of the batch, but everything written HERE (the supernode, a workbook's
    structure, a folder tree, code symbols) is outside that pipeline and was
    surviving only if some later document ingest happened to flush on its way
    past. Whatever was written after the last such flush was silently lost --
    which is why code symbols, written last, never reached disk at all.
    """
    await rag.chunk_entity_relation_graph.index_done_callback()
    await rag.entities_vdb.index_done_callback()


async def upsert_node(
    rag,
    node_id: str,
    label: str,
    description: str,
    file_path: str,
    source_id: str,
    keep_existing_label: bool = False,
    index: bool = True,
    **properties,
) -> None:
    """Write one labelled node, and index it so retrieval can find it.

    `keep_existing_label` is for nodes that may already exist from a document:
    a customer name that arrives as a spreadsheet cell must not overwrite the
    `Organization` label the contract gave it.

    `index=False` keeps a node out of the entity vector store. A repo
    contributes one node per class, function and method; indexing all of them
    would bury the documents under thousands of symbol cards, so code symbols
    are graph-only and only the file-level nodes above them get a card.
    """
    if label not in NODE_LABELS:
        raise ValueError(f"{label!r} is not a node label: {sorted(NODE_LABELS)}")

    existing = await rag.chunk_entity_relation_graph.get_node(node_id)
    if existing is not None and keep_existing_label:
        return

    await rag.chunk_entity_relation_graph.upsert_node(
        node_id,
        node_data={
            "entity_id": node_id,
            "entity_type": label,
            "description": description,
            "source_id": source_id,
            "file_path": file_path,
            "created_at": int(time.time()),
            **properties,
        },
    )
    if not index:
        return
    await rag.entities_vdb.upsert(
        {
            compute_mdhash_id(node_id, prefix="ent-"): {
                "entity_name": node_id,
                "content": f"{node_id}\n{description}",
                "source_id": source_id,
                "file_path": file_path,
            }
        }
    )


async def upsert_edge(
    rag,
    source: str,
    target: str,
    rel_type: str,
    description: str,
    file_path: str,
    source_id: str,
    weight: float = 1.0,
    **properties,
) -> None:
    """One typed edge. `properties` carries whatever the relationship itself
    means -- a CALLS edge's call site and confidence, for instance."""
    if rel_type not in REL_TYPES:
        raise ValueError(f"{rel_type!r} is not a relationship type: {sorted(REL_TYPES)}")

    await rag.chunk_entity_relation_graph.upsert_edge(
        source,
        target,
        edge_data={
            "weight": weight,
            "description": description,
            "keywords": rel_type,
            "source_id": source_id,
            "file_path": file_path,
            "created_at": int(time.time()),
            # The store is an UNDIRECTED networkx graph: it keeps the pair, not
            # the order, and reads it back in whichever order the nodes were
            # added. For RELATED_TO that never mattered. For CALLS it is the
            # entire content of the edge -- "A calls B" read back as "B calls
            # A" is not a weaker answer, it is a wrong one. So the direction
            # travels as data on the edge, where the store cannot lose it.
            "rel_from": source,
            "rel_to": target,
            **properties,
        },
    )


async def update_node(rag, node_id: str, **properties) -> None:
    """Merge properties into a node that already exists.

    Not `upsert_node`: that restates the label, description and provenance,
    which a status flip or a late `doc_id` has no business rewriting. A missing
    node is a no-op -- the caller is patching, not creating.
    """
    existing = await rag.chunk_entity_relation_graph.get_node(node_id)
    if existing is None:
        return
    await rag.chunk_entity_relation_graph.upsert_node(
        node_id, node_data={**existing, **properties}
    )


async def remove_nodes(rag, node_ids: list[str]) -> None:
    """Drop nodes and their vector records. Incident edges go with them."""
    if not node_ids:
        return
    await rag.chunk_entity_relation_graph.remove_nodes(node_ids)
    await rag.entities_vdb.delete(
        [compute_mdhash_id(node_id, prefix="ent-") for node_id in node_ids]
    )


if __name__ == "__main__":
    assert canonical_label("organization") == "organization"
    assert canonical_label("Organization") == "organization"
    # What LightRAG itself writes: `entity_type.replace(" ", "").lower()`.
    assert canonical_label("data source") == "datasource"
    assert canonical_label("") == UNRESOLVED
    assert canonical_label(None) == UNRESOLVED
    assert canonical_label("UNKNOWN") == UNRESOLVED

    assert canonical_name("Microsoft") == canonical_name("microsoft") == "MICROSOFT"
    assert canonical_name("the Microsoft") == "MICROSOFT"
    assert canonical_name("Microsoft's") == canonical_name("Microsoft’s") == "MICROSOFT"
    assert canonical_name('"Acme Corp".') == "ACME CORP"
    assert canonical_name("  Real   Madrid ") == "REAL MADRID"
    assert canonical_name("") == canonical_name(None) == ""
    assert canonical_name("Theodore") == "THEODORE", "only a whole article is dropped"

    # Separators fold: a caption, a slug and prose are one node.
    assert (
        canonical_name("LIONEL_MESSI")
        == canonical_name("Lionel-Messi")
        == canonical_name("Lionel Messi")
        == "LIONEL MESSI"
    )
    assert canonical_name("RONALDO_") == "RONALDO", "a trailing separator is not a name"
    assert canonical_name("Jean-Claude Van Damme") == "JEAN CLAUDE VAN DAMME"
    # An en/em dash is a separator too -- the same name, typeset differently.
    assert canonical_name("Lionel–Messi") == "LIONEL MESSI"
    # Distinct people stay distinct: folding separators must not fold names.
    assert canonical_name("Ronaldo Jr") != canonical_name("Ronaldo")

    assert TABULAR_LABELS <= NODE_LABELS and DOCUMENT_LABELS <= NODE_LABELS
    assert STRUCTURE_LABELS <= NODE_LABELS and SOURCE in NODE_LABELS
    # The label the ontology admits is what canonical_label() produces, or
    # every write of it is rejected at the boundary.
    assert CODE_FILE == "codefile" and canonical_label("CodeFile") in NODE_LABELS
    assert RELATED_TO in REL_TYPES and "MENTIONS" not in REL_TYPES
    assert {HAS_ROOT, CONTAINS_FILE, CALLS, IMPLEMENTS} <= REL_TYPES

    assert edge_category(CALLS) == BEHAVIORAL
    assert edge_category(CONTAINS_FILE) == STRUCTURAL
    assert edge_category(RELATED_TO) == SEMANTIC
    # An older ingest's free-text keyword, and an edge with none at all.
    assert edge_category("mentions in passing") == SEMANTIC
    assert edge_category(None) == SEMANTIC
    assert set(_EDGE_CATEGORY) <= REL_TYPES, "no category for a type nothing writes"
    assert CODE_LABELS <= STRUCTURE_LABELS
    print("ok")
