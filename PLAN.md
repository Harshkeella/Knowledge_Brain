# nodeRels — core plan (v2: storage & retrieval rebuild)

Everything above the storage layer stays: same API surface, same SSE contract,
same Next.js UI, same Chrome extension, same LightRAG orchestration, same local
embeddings / GLiNER extraction / cross-encoder rerank. This plan replaces only
**where things are stored and how they come back out**, plus a real design for
tabular data.

## 1. Vector store: LightRAG's NanoVectorDB → Qdrant (dense + sparse)

**Problem.** NanoVectorDB is a JSON file scanned in-process, dense-cosine only.
Pure semantic similarity misses exact tokens — a part number, an error code, a
column header, a person's surname — because MiniLM embeds them into a fuzzy
neighbourhood. Retrieval quality is capped by having one signal.

**Change.** One Qdrant collection per LightRAG namespace (`chunks`, `entities`,
`relationships`), each with **two named vectors**:

| Vector | What | Answers |
|---|---|---|
| `dense` | MiniLM-L6-v2, 384d, cosine | semantic / paraphrase |
| `sparse` | BM25-style term vector, IDF modifier | keyword / exact token |

Query is a single Qdrant Query API call: two `Prefetch` branches (dense +
sparse) fused server-side with **RRF**. The dense branch keeps
`cosine_better_than_threshold` as its quality bar; the sparse branch is
unfiltered so a rare exact term can rank on its own.

- **Deployment**: embedded by default (`QdrantClient(path=storage/qdrant)`) —
  no Docker, no server. Set `QDRANT_URL` to point at a real Qdrant instead;
  nothing else changes.
- **Sparse encoding** is local and dependency-free: regex tokenize → lowercase
  → stopword drop → `blake2b`→uint32 term id → BM25 tf saturation. IDF is
  computed by Qdrant via `Modifier.IDF`.
- Implemented as `HybridQdrantStorage`, a subclass of LightRAG's
  `QdrantVectorDBStorage` registered into LightRAG's storage registry —
  4 overrides (`initialize`, flush, `query`, `get_vectors_by_ids`), everything
  else (buffering, deletes, workspaces, read-your-writes) inherited.

**Migration**: vectors do not move. `backend/storage/kb/vdb_*.json` is dead;
delete `backend/storage/kb` and re-ingest once.

## 2. Graph: a labeled property graph (the Neo4j model, not Neo4j)

**Problem.** Today's graph is a flat entity graph: nodes carry a free-text
`entity_type`, edges carry no type at all — just a description and keywords.
You cannot ask "which columns feed this metric" or "which contracts mention
this customer" because the edges don't mean anything.

**Change.** Keep the storage (NetworkX on disk — no server, no Neo4j). Impose
the property-graph *model* on top, enforced on write in
`app/services/graph_schema.py`:

- **Node labels** from a closed ontology — the document ontology
  (`Person`, `Organization`, `Location`, `Product`, `Technology`, `Event`,
  `Concept`, `Date`) plus the tabular ontology (`Workbook`, `Worksheet`,
  `Column`).
- **Typed relationships** from a closed vocabulary, written to the edge's
  `keywords` property: `RELATED_TO`, `HAS_SHEET`, `HAS_COLUMN`,
  `DERIVED_FROM`, `HAS_VALUE`.
- **Properties** on both, validated: unknown labels/rel types are rejected at
  the boundary rather than silently written.

The `/api/v1/graph` payload gains nothing new-shaped: `entity_type` carries the
label, `keywords` carries the relationship type. The Graph Explorer colors by
`entity_type` already and picks up the new labels with **zero frontend change**.

Ceiling, stated up front: NetworkX stores one edge per node pair, so two
different relationship types between the same two nodes collide. That never
happens in the tabular projection (distinct pairs) and is what LightRAG already
does for entities. Swap in Kùzu/Memgraph if multi-edges ever matter.

## 3. Tabular data — the part that has to be 100% correct

**Principle, unchanged and non-negotiable: no cell value is ever answered by a
language model.** DuckDB holds the rows and does every calculation; the LLM
only writes SQL that is parsed, bound against the real catalog, and row-capped
before a single row is read.

What changes is everything *around* that:

### 3a. Ingest (`parsers/spreadsheet.py`)
Already correct and stays: number-format-driven typing (Excel serial dates,
currency, percentages), formula capture, formula lineage per column, one
DuckDB table per worksheet, re-upload replaces.

### 3b. Store — project the schema into the property graph
Today a workbook contributes one prose blob that GLiNER may or may not pick
apart. Instead, write the structure **deterministically** into the graph — no
LLM, no extraction, so it is exactly right every time:

```
(:Workbook {file_name, sheets, rows})
   -[:HAS_SHEET]->  (:Worksheet {table, rows, columns})
                       -[:HAS_COLUMN]-> (:Column {name, data_type, semantic, formula})
(:Column) -[:DERIVED_FROM]-> (:Column)   # from the captured formula lineage
(:Column) -[:HAS_VALUE]->    (existing entity)   # bridge to the documents
```

`HAS_VALUE` is the bridge the current design lacks: a `Customer` column whose
values are `Acme Corp`, `Globex` links those cells to the `Acme Corp` entity
already extracted from a contract PDF. It only ever links to a node that
*already exists* — a spreadsheet never invents entities, so there is nothing to
cap and nothing to clean up, and the edge appears exactly where it means
something. Distinct values are read `SPREADSHEET_MAX_GRAPH_VALUES` at a time.
**Rows never enter the graph.**

### 3c. Retrieve — schema cards, not a schema dump
One **schema card** per column and per table is indexed into Qdrant (dense +
sparse). A question retrieves the relevant cards, and only those tables' DDL is
put in front of the SQL writer. Two wins: exact column-name matches land via
the sparse vector, and the prompt stops growing with every workbook uploaded.

Routing also stops being a coin flip. Today *any* existing spreadsheet sends
*every* message through the SQL router first. New rule: the SQL path runs only
when schema cards actually retrieve above threshold; otherwise the document RAG
path takes over — with no extra LLM call in the common case.

### 3d. Visualize — free
Workbook / Worksheet / Column / Value nodes are ordinary graph nodes, so the
existing Graph Explorer renders the spreadsheet's structure and its links to
documents with no frontend work: labels get their own palette slots, the detail
panel shows type/semantic/formula from the node properties.

## Phases

1. **Qdrant hybrid store** — sparse encoder, `HybridQdrantStorage`, config,
   registry wiring, test. *(storage swap; re-ingest required)*
2. **Property-graph schema** — ontology module + validation, applied to
   extraction output and the graph API.
3. **Tabular projection + schema-card retrieval + routing fix.**
4. **Docs** — README rewritten to match.

## What this plan deliberately does not do

- No Neo4j / Memgraph / Kùzu server. The property graph is a schema, not a
  daemon.
- No reranker or embedding-model change. Retrieval quality is being fixed at
  the *recall* end (hybrid), not the precision end, which the cross-encoder
  already handles.
- No UI changes. Every improvement lands through data the existing pages
  already render.
