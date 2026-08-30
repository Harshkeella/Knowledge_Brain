<div align="center">

<img src="logo.png" width="140" alt="nodeRels" />

# nodeRels — GraphRAG Knowledge Base

**Turn documents, URLs, and YouTube videos into a searchable knowledge graph you can chat with.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js 16](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![LightRAG](https://img.shields.io/badge/RAG-LightRAG%20hybrid-6E56CF)](https://github.com/HKUDS/LightRAG)
[![Embeddings: local](https://img.shields.io/badge/Embeddings-local%20%2F%20no%20API%20key-2EA043)](https://www.sbert.net/)

[Quick start](#prerequisites) · [How it works](#project-flow) · [API](#api-surface-backend) · [Chrome extension](#chrome-extension) · [Layout](#repository-layout)

</div>

---

A full-stack hybrid **vector + knowledge-graph RAG** application. Feed it documents,
URLs, YouTube videos, or pasted text, and it builds a searchable knowledge base
combining vector similarity search with an automatically extracted entity/relationship
graph (via [LightRAG](https://github.com/HKUDS/LightRAG)).

The project has two independently runnable parts:

- **`backend/`** — FastAPI service that owns ingestion, deduplication, the LightRAG
  engine, and the document inventory.
- **`frontend/`** — Next.js dashboard for uploading content, browsing the knowledge
  base, chatting with it, and (soon) exploring the graph.

---

## Tech stack

### Backend
| Layer | Choice |
|---|---|
| Framework | FastAPI + Uvicorn |
| RAG engine | [LightRAG](https://github.com/HKUDS/LightRAG) (`lightrag-hku`) — hybrid vector + graph retrieval |
| Graph extraction | Local [GLiNER](https://github.com/urchade/GLiNER) encoder (`gliner_small-v2.1`), zero-shot against the `ENTITY_LABELS` ontology — one forward pass per window instead of one LLM call per chunk, so ingest isn't bound by an API rate limit. `EXTRACTION_BACKEND=llm` restores the Groq/Ollama path |
| LLM | Split by role via LightRAG's `role_llm_configs`: Groq (`llama-3.3-70b-versatile`) for keyword extraction (and for graph building when `EXTRACTION_BACKEND=llm`), OpenRouter (`openrouter/free`, OpenRouter's auto-router over its free models, by default) for answering (chat query generation) — kept on separate keys so ingest and chat don't compete for the same quota. Both fall back to a local Ollama model if their key is unset or the call fails |
| Embeddings | Local, via `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim) — no API key required |
| Document parsing | PyMuPDF (PDF), `trafilatura` (web articles), `youtube-transcript-api` (YouTube transcripts) |
| Metadata store | SQLite (`aiosqlite`) — tracks file name, source type, hash, chunk count, size, date added |
| Vector store | **Qdrant**, two named vectors per point — `dense` (MiniLM, cosine) and `sparse` (BM25 term vector, IDF) — fused server-side with RRF, so retrieval has a semantic *and* a keyword half. Embedded by default (no server, no Docker); set `QDRANT_URL` to use a real instance |
| Knowledge graph | A **labeled property graph** (Neo4j's model, no Neo4j): every node carries a label from a closed ontology, every deterministic edge a relationship type. Persisted locally, no graph server to run |
| Storage | Qdrant (`backend/storage/qdrant/`) for vectors, LightRAG's KV/graph files under `backend/storage/kb/`, DuckDB (`spreadsheets.duckdb`) for tabular data |

### Frontend
| Layer | Choice |
|---|---|
| Framework | Next.js 16 (App Router) + React 19 + TypeScript |
| Styling | Tailwind CSS 4 |
| Components | shadcn/ui on top of `@base-ui/react` |
| State | Zustand |
| Icons | lucide-react |

---

## Project flow

1. **Ingest** — a user uploads a file (PDF/TXT/MD, or a spreadsheet — see
   [Spreadsheets](#spreadsheets)), submits a URL (web article or YouTube link),
   or pastes raw text from the **Knowledge Base** page.
2. **Extract** — the backend picks the right parser (`app/services/parsers/`) to pull
   plain text out of the source (PDF text, article body via `trafilatura`, or a
   YouTube transcript).
3. **Deduplicate** — the text is normalized and SHA-256 hashed
   (`app/services/dedup.py`). If a document with the same hash already exists, the
   existing record is returned instead of re-ingesting.
4. **Index** — the text is handed to the shared `LightRAG` instance
   (`app/services/lightrag_engine.py`), which:
   - chunks it (512 tokens, ~10% overlap),
   - embeds chunks locally via `sentence-transformers` and writes them to
     Qdrant with a BM25 sparse vector alongside the dense one
     (`app/services/qdrant_store.py`, `app/services/sparse.py`),
   - extracts entities and relationships into a knowledge graph with a local
     GLiNER encoder (`app/services/gliner_extract.py`) scored against the
     `ENTITY_LABELS` ontology — one forward pass per ~150-word window, no API
     and no rate limit. Set `EXTRACTION_BACKEND=llm` to go back to per-chunk
     Groq/Ollama extraction, which is bound by tokens-per-minute (~2
     chunks/min) rather than by model speed,
   - normalizes every entity type to a canonical property-graph label
     (`app/services/graph_schema.py`),
   - persists the graph and KV stores to `backend/storage/kb/`.
5. **Track** — a metadata row (doc id, file name, source type, chunk count, size,
   timestamp) is written to the SQLite manifest (`backend/storage/manifest.sqlite3`)
   so the frontend can list/delete documents without querying LightRAG directly.
6. **Browse / manage** — the **Knowledge Base** dashboard page lists every ingested
   document (via `GET /api/v1/knowledge-base`) and lets you delete one (via
   `DELETE /api/v1/knowledge-base/{doc_id}`), which removes it from both LightRAG and
   the manifest.
7. **Query** — the **Chat** page sends each message to
   `POST /api/v1/chat/stream` (`app/api/chat.py`). It first checks whether the
   question retrieves any worksheet/column node (see [Spreadsheets](#spreadsheets));
   if not, it runs LightRAG's `mix`-mode retrieval (hybrid vector + graph) —
   every vector lookup fuses the dense and sparse branches with RRF, so an exact
   term like an error code or a surname is findable even when its embedding
   isn't close to the question's. It then re-ranks the retrieved chunks with a
   local cross-encoder (`app/services/reranker.py`), caps the assembled context at
   `QUERY_CONTEXT_TOKEN_BUDGET`, and streams the response back over Server-Sent Events:
   a `sources` event first (the retrieved reference/file list), then a `token` event
   per generated chunk, then `done`. The UI renders tokens as they arrive and shows a
   collapsible source list under the answer.
8. **Explore the graph** — the **Graph** page renders an interactive force-directed
   view (2D canvas or 3D WebGL, toggle in the toolbar) of the extracted entity graph,
   fetched from `GET /api/v1/graph`. Nodes are colored by their property-graph
   label — `Person`, `Organization`, …, plus `Workbook`, `Worksheet` and `Column`
   for anything ingested from a spreadsheet (a fixed,
   frequency-ranked categorical palette — see `frontend/src/components/graph/entity-colors.ts`),
   sized by degree, and searchable by name. Clicking a node opens a detail panel with
   its description, source file, and relationships. Above `max_nodes`, LightRAG keeps
   the highest-degree nodes and returns the induced subgraph; nodes left with no
   surviving edges are dropped so they don't float free in the layout.

### Spreadsheets

An uploaded `.xlsx`/`.xlsm`/`.csv` takes a different route: rows never enter the
graph. Each worksheet becomes its own table in a DuckDB file
(`backend/storage/spreadsheets.duckdb`) with real types — Excel keeps dates as
serial numbers and currency/percentages as plain floats, so the cell's *number
format* is what decides the column type, not the raw value. Original formulas
are kept as column metadata, along with which columns each one is derived from.

**Structure goes into the graph, values do not.** The workbook's shape is
*written* into the property graph rather than extracted from prose, so it is
exactly right every time (`app/services/tabular_graph.py`):

```
(:Workbook) -[:HAS_SHEET]-> (:Worksheet) -[:HAS_COLUMN]-> (:Column)
(:Column)   -[:DERIVED_FROM]-> (:Column)          from the captured formula
(:Column)   -[:HAS_VALUE]->    (existing entity)  the bridge to your documents
```

`HAS_VALUE` only ever links to a node that already exists — so a `Customer`
column whose cells read `Acme Corp` connects to the `Acme Corp` organization
already extracted from a contract PDF, while a value no document mentions stays
out of the graph entirely. That means a spreadsheet can never invent entities,
and deleting one leaves your documents' entities untouched.

Every one of those nodes is indexed in the vector store, description and all,
which makes each column its own retrieval card. Two things follow:

- **Routing stops being a guess.** A question reaches the SQL path only when it
  actually retrieves a worksheet or column node — so a question about your
  documents never costs an LLM round-trip to discover it wasn't about the data.
- **The SQL prompt stays small.** Only the retrieved tables' schema is put in
  front of the model, whether you have one workbook loaded or fifty.

From there `app/services/spreadsheet_query.py` takes over: the LLM writes DuckDB
SQL, **DuckDB does every piece of arithmetic**, and the result comes back as a
structured `table` SSE event rendered by `DataResultTable` instead of a wall of
markdown. Asking for a computed column ("add Profit = Revenue - Cost") goes down
a separate write path (`ALTER TABLE ADD COLUMN` + `UPDATE`) and is undoable from
the widget.

Guardrails, in order: the generated statement must parse as exactly one
`SELECT`; every table and column it names must bind against the real schema
before a row is read; the store has filesystem access disabled so a query can't
reach `read_csv`; results are capped at `SPREADSHEET_MAX_ROWS`. A failure is fed
back to the model to retry, twice, before the user sees an error.

The Graph Explorer renders all of this: `Workbook`, `Worksheet` and `Column`
are ordinary labels, so a spreadsheet's structure — and its links into your
documents — shows up in the same view as everything else. A workbook is only a
handful of low-degree nodes, though, so it never survives the whole-graph view's
top-degree cut: pick it from the **focus dropdown** in the toolbar to center the
view on it instead.

### Sources and folders

Every ingestion — a PDF, a URL, a YouTube video, a pasted note, a whole folder —
creates one **`Source` node**: the addressable root of everything that upload
produced, carrying its name, type, timestamp, byte size and counts. Entities
already recorded which file they came from in a property; the Source node is
what makes that a single hop you can *click*, so "show me everything from this
upload" is one edge away rather than a property filter
(`app/services/source_graph.py`).

```
(:Source {source_type:"pdf"})    -[:HAS_ROOT]-> (every entity that PDF produced)
(:Source {source_type:"folder"}) -[:HAS_ROOT]-> (:Folder)
```

Pick any source from the **focus dropdown** in the Graph Explorer (or hit the
share icon on its inventory row) to center the view on it. Source nodes are hubs
by design, so they are opt-in rather than part of the default whole-graph view.

**Folder ingestion** (`POST /api/v1/ingest/folder`, or the *Add a folder* card)
takes a path on the server's own disk and mirrors the tree into the graph —
`.git`, `node_modules`, `__pycache__`, `venv`, build output and friends skipped
by default, extendable with a `.kbignore` in the folder root:

```
(:Folder) -[:CONTAINS_FOLDER]-> (:Folder)
(:Folder) -[:CONTAINS_FILE]->   (:File | :CodeFile | :Image | :Video)
```

The walk is unbounded: every subfolder, file, image, video and source file,
all the way down, with the ignore list pruned at every level rather than only
at the root. `FOLDER_MAX_DEPTH` exists to *clip* a pathological tree and
defaults to a sentinel (1000) far past any real one — a small default there is
exactly the bug that looks like "the walk stopped early".

**Two passes, and the order is the whole point.** The tree and its code symbols
are written and committed first; only then are document leaves (PDF/MD/TXT/XLSX)
routed through the ordinary ingestors, as a background task the HTTP response
does not wait for. Interleaving them — which is what the first version did —
put minutes of LLM extraction inside the tree-write loop *and* inside the
request, so a client timeout cancelled the walk with `CancelledError` (a
`BaseException`, so no `except Exception` caught it) and the closing flush never
ran. Everything past the first document leaf was silently lost: on one real
repo, 30 of 33 file nodes. The tree is now durable before any slow work starts,
and a document that fails costs you a `doc_id`, never a subtree.

The `Source` node carries the lifecycle and the shape of what landed, so
"did this tree come in whole?" is a property read rather than a graph query:

| Property | Meaning |
|---|---|
| `status` | `processing` while documents are still indexing, then `completed` or `failed` |
| `total_folders` / `total_files` | What the walk found |
| `max_depth_reached` | Deepest `Folder.depth`, relative to the ingestion root (root = 0) |
| `documents_indexed` | Document leaves that made it through the ingestors |

Untick *Also index...* for a structure-only scan, which skips the second pass
entirely and finishes `completed` immediately.

`FOLDER_INGEST_ROOT` confines folder ingestion to one subtree. Leave it unset
for the local single-user default; set it before binding the API to anything
but localhost, because the path parameter reaches the filesystem.

Verify a tree against the filesystem at any time:

```bash
python -m scripts.verify_depth C:/path/to/repo         # re-ingest, then compare
python -m scripts.verify_depth --check <source name>   # compare what is stored
```

It reports folders, files and max depth side by side with `os.walk`'s own
answer, plus the zero-gap chain check: every `Folder` at depth N hangs off
exactly one `Folder` at depth N-1.

### Hop exploration

The Graph Explorer opens on **`Source` nodes only** — exactly one per
ingestion, no edges, nothing else — and you **double-click a node to pull in
one more hop**, the way Neo4j Browser has always worked. This is not a smaller
version of the full graph: the full load is a degree-prioritised BFS bounded by
`max_depth`/`max_nodes`, so a folder eight levels down is not missing from the
graph, it is past the horizon of the only query that was ever asked. Hopping is
the only way to reach it.

```
double-click a Source     -> its root, via HAS_ROOT
double-click a Folder     -> child Folders and Files, via CONTAINS_FOLDER/CONTAINS_FILE
double-click a File       -> its Classes and Functions, via DEFINES/DEFINES_METHOD
double-click a Function   -> its call neighbourhood, via CALLS — both directions
```

Results are merged into what is already on screen and deduped by id, so two
different paths to the same node add the edge and not a second node. Nodes
already laid out keep their position; only the new region is simulated. The old
dense view is one **Show full graph** click away and otherwise unchanged.

Anything ingested before the `Source` node existed has nothing to double-click.
`python -m scripts.backfill_sources --apply` writes the missing ones.

### Code intelligence

Source files get their symbols hung off the `CodeFile` leaf
(`app/services/code_intel.py`):

```
(:CodeFile) -[:DEFINES]->        (:Class | :Function)
(:Class)    -[:DEFINES_METHOD]-> (:Method)
(:Class)    -[:INHERITS]->       (:Class)
(:Function | :Method) -[:CALLS]-> (:Function | :Method)
(:CodeFile) -[:IMPORTS]->        (:CodeFile)
```

**Python needs no extra dependency** — extraction runs on the standard library's
`ast`. Other languages go through tree-sitter, which needs grammars from the
optional extra; without it, code files are still ingested as `CodeFile` leaves
and simply carry no symbols. Nothing raises either way.

```bash
pip install -r requirements-codeintel.txt   # tree-sitter grammars + Pillow
```

Call resolution is by name, file scope first and project-wide second, and
**only when the name is unambiguous**. A resolved call carries `resolved: true`
and a `confidence` (1.0 file-local, 0.8 project-wide unique), plus its
`call_site_line`. There is no type inference and no dispatch on receiver type,
so `self.save()` in a file defining two `save` methods never becomes an edge to
either one: a wrong `CALLS` edge is worse than a missing one, because nothing
downstream can tell it is wrong.

**Unresolved does not mean invisible.** A call whose target is outside the
scanned tree but traceable to an import — `httpx.get()`, `Counter()` — becomes
a deduped `ExternalSymbol` node with a `module_guess`, and a real `CALLS` edge
with `resolved: false`:

```
(:Function) -[:CALLS {resolved:false}]-> (:ExternalSymbol {name, module_guess})
```

An import is the only signal available without type inference, and it is
deliberately the *whole* filter: `len()`, `append()`, `self.save()` stay in
`calls_unresolved` on the caller. Giving every unresolved name a node would add
hundreds of hubs like `len` and `str` wired to half the codebase, which makes a
call graph less readable, not more.

`Function`/`Method` nodes carry `calls_in_count` and `calls_out_count`,
computed at write time, so hotspots (`ORDER BY calls_in_count`) and dead code
(`calls_in_count == 0`) are one property read rather than a traversal — and the
counts stay true even when the loaded subgraph is truncated.

Symbol nodes are graph-only — they are deliberately kept out of the entity
vector store, or one repo would bury every document under thousands of symbol
cards. The `CodeFile` and `Folder` nodes above them are indexed as usual.

Stated ceilings: only module-level definitions and class methods become nodes
(a closure's calls are attributed to the function enclosing it), and the graph
store keeps one edge per node pair — so `CALLS` carries its own `rel_from` /
`rel_to`, because the underlying store is undirected and would otherwise read
"A calls B" back as "B calls A". Repeated calls to the same target collapse
onto that one edge, which records the first `call_site_line` and a `call_count`.

`INHERITS` is class→class. `IMPLEMENTS` is class→interface and is only emitted
for languages that have interfaces (TS, Java); Python always gets `INHERITS`.

### Edge categories

Every edge has an `edge_category`, derived from its relationship type rather
than stored on it — that way it covers the edges LightRAG's own extractor
wrote, with no backfill and nothing to keep in sync:

| Category | Relationships | Drawn as |
|---|---|---|
| `structural` | `HAS_ROOT`, `CONTAINS_FOLDER`, `CONTAINS_FILE`, `DEFINES`, `DEFINES_METHOD`, `HAS_SHEET`, `HAS_COLUMN` | thin grey, no arrowhead |
| `behavioral` | `CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`, `DERIVED_FROM` | indigo, arrowed (dashed for everything but `CALLS`) |
| `semantic` | everything the entity extractor produced (`RELATED_TO`) | unchanged |

### Persisting direct graph writes

LightRAG's storage says it plainly: *"Callers outside the pipeline must persist
explicitly."* `upsert_node`/`upsert_edge` only mutate memory — inside
`ainsert()` the pipeline commits at the end of its batch, but the supernode, a
workbook's structure, a folder tree and its code symbols are all written
*outside* that pipeline. Every one of those write paths ends in
`graph_schema.flush()`. Without it they survive only if a later document ingest
happens to flush on its way past, and whatever came after the last such flush —
code symbols, which are written last — never reaches disk at all.

If a folder was ingested before this, re-scan it:

```bash
python -m scripts.reingest_folders           # dry run: nodes on disk vs expected
python -m scripts.reingest_folders --apply   # re-scan every folder source

python -m scripts.verify_depth --check <name>   # is that tree complete?
python -m scripts.backfill_sources --apply      # Source nodes for older ingests
```

### Node symbols

Every node type has one icon, resolved from its `entity_type` by a single
registry (`frontend/src/constants/symbols.ts`) that feeds the Graph Explorer
legend, the node detail panel and the inventory table. The icon is *derived*,
not stored on the node: `entity_type` already determines it, so a `symbol`
property would be a denormalised copy that drifts the first time a label is
added on one side only.

| Node | `entity_type` | Icon |
|---|---|---|
| Source supernode | `source` (+ `source_type`) | `FolderCog` / `Globe` / `CirclePlay` / `FileText` / `FileType` |
| Folder | `folder` | `Folder` |
| File | `file` | `FileText` |
| Code file | `codefile` | `FileCode` |
| Class | `class` | `Box` |
| Function / Method | `function` / `method` | `SquareFunction` |
| Image | `image` | `Image` |
| Video / audio | `video` | `CirclePlay` |
| Workbook / Worksheet / Column | `workbook` / `worksheet` / `column` | `Sheet` / `Table2` / `Columns3` |
| Anything the extractor found in prose | the ontology label | colored dot |

Structural labels hold a **fixed** colour; document and tabular entity types
keep the frequency-ranked categorical palette they already used, so existing
graphs render exactly as before.

The icon is drawn on the node itself, not only in the legend. A canvas cannot
render a React component, so each glyph is rasterized once from **lucide's own
geometry** (`__iconNode`, imported rather than hand-copied, so a node's glyph
can never diverge from the legend's) and blitted onto every node of that type.
Below `ICON_MIN_ZOOM` or `ICON_MIN_RADIUS_PX` (`node-icons.ts`) nodes stay plain
dots — hundreds of overlapping glyphs in a dense cluster is more clutter, not
less — except the selected node, which always draws its icon so you can tell
what you are pointing at.

### Tracing a call chain

Click any code node and the graph focuses its call neighbourhood: the node,
everything it calls, everything that calls it, and those edges stay at full
opacity while the rest dims. The side panel splits into **Calls (N)** and
**Called by (N)** — counts from the stored totals, entries clickable, so a call
chain can be walked hop by hop. *Clear focus* leaves the mode.

**Code only** in the toolbar hides every non-code node type and every semantic
edge, leaving a clean architecture/call-graph view instead of the mixed
entity+code graph.

### Chat sessions and answer provenance

The app lands on **chat** (`/dashboard/chat`); the knowledge base and graph
stay exactly as they were, reachable from the chat sidebar and the shell nav.

**Sessions** live in their own SQLite file, `storage/chat.sqlite3` — not in the
graph and not in Qdrant. A session list is mutable, chronological, per-user app
state, which is the wrong shape for either store, and keeping it separate means
a knowledge-base rebuild (which deletes `storage/kb`) never takes the chat
history with it.

| Table | Columns |
|---|---|
| `chat_sessions` | `id`, `title`, `created_at`, `updated_at` |
| `chat_messages` | `id`, `session_id` → cascade, `role`, `content`, `evidence` (JSON), `created_at` |

Titles are generated from the first message on the cheap extraction model and
fall back to the truncated question, so a session is never untitled. Appending
a message bumps `updated_at`, which is what orders the sidebar — a thread you
replied to today sorts to the top rather than staying buried at its creation
date. The Today/Yesterday/Previous 7 Days grouping happens on the client
(`lib/session-groups.ts`), because the buckets are relative to the *viewer's*
midnight and an API that grouped them would bake one timezone into the response.

**Provenance.** `services/provenance.py` turns LightRAG's returned
`data.entities` / `.relationships` / `.chunks` into one evidence chain per
source. Those arrays are what LightRAG produces *after* truncation, so they are
what actually reached the model — not the candidate set that was considered,
which is the distinction that makes the panel worth opening.

Every source shown with an answer gets a route icon that opens the chain as a
**linear step-flow**, deliberately not the force-directed graph from hop
exploration: three to a dozen nodes in a physics sim jitter into a shape that
says nothing, and the chain's direction is itself the claim.

```
Source document → Matched text → Entity → Relationship → … → Answer
```

Icons and fixed colours come from the same `constants/symbols` table the graph
canvas reads, so an entity looks the same in both. Entities and relationship
endpoints deep-link into the explorer through its existing `?focus=` parameter
rather than reimplementing exploration inside the panel. The chain is persisted
on the assistant message, so reopening an old session still opens working
panels.

### Retrieval accuracy

| Lever | Where | What it does |
|---|---|---|
| Ontology + canonicalization | `services/graph_schema.py` | Closed node/relationship vocabularies enforced at the two upsert chokepoints. Already live; still the largest single lever |
| Grounding check | `services/grounding.py` | After the answer completes, flags sentences the evidence doesn't support |
| Multi-hop decomposition | `services/multihop.py` | Splits a two-fact question, retrieves each hop, seeds the entities it found into the final retrieval's keywords |
| Context curation | `api/chat.py` | `chunk_top_k` + the derived entity/relation token budgets keep the strongest evidence from being diluted |
| Continuous eval | `scripts/eval_retrieval.py` | Turns all of the above into a number you can diff |

**Grounding** flags rather than strips, and that is a consequence of streaming:
the answer is already on screen token by token, so removing a claim would mean
buffering the whole response and giving up progressive rendering. The verdict
rides out on its own `grounding` SSE frame and the UI attaches it to the
message. The test is lexical rather than an LLM judge — it catches the failure
that matters (a fluent sentence of names and numbers that appear nowhere in
what was retrieved) without a second model call per answer.

**Multi-hop** does not add a second generation path; the answer still streams
from one `aquery_llm` call. What changes is what that call searches for. Each
sub-question is retrieved first with `aquery_data` (no LLM), the entities those
hops found are ranked by how many hops saw them, and the top ones are seeded
into `ll_keywords` — LightRAG skips its own keyword extraction when keywords are
pre-supplied, so the final hop searches for hop-1's discoveries by name. A cheap
regex gate decides whether to decompose at all, so ordinary one-hop questions
pay nothing.

**Eval.** `scripts/eval_questions.yaml` scores *retrieval*, not prose: each
question lists substrings that must appear in the assembled evidence chain, so
a model swap or a temperature change can't look like an accuracy regression.

```bash
cd backend
python -m scripts.eval_retrieval --save before.json
# ... change retrieval or ontology logic ...
python -m scripts.eval_retrieval --compare before.json
```

Rerun it whenever retrieval or ontology logic changes, and add a row whenever a
real question comes back wrong — that is what makes accuracy a tracked number
instead of a one-off judgment call.

### API surface (backend)

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/ingest/file` | Upload one or more files (PDF/TXT/MD/XLSX/CSV) |
| POST | `/api/v1/ingest/url` | Ingest a web article or YouTube URL |
| POST | `/api/v1/ingest/text` | Ingest pasted text |
| POST | `/api/v1/ingest/folder` | Scan a folder on the server's disk into the graph (`path`, `name`, `index_documents`). Returns once the tree is committed; document leaves index in the background, tracked by `Source.status` |
| GET | `/api/v1/knowledge-base` | List all ingested documents |
| DELETE | `/api/v1/knowledge-base/{doc_id}` | Remove a document (or a whole folder source, cascading its tree and code symbols) from the KB |
| DELETE | `/api/v1/knowledge-base/spreadsheet/{table}/columns/{column}` | Undo a computed column added from chat |
| POST | `/api/v1/chat/stream` | Ask a question; streams an SSE response (`sources` → `evidence` → `token`×N → `grounding`? → `done`, or `table` → `token` → `done` for spreadsheet questions). Optional `session_id` persists the turn |
| POST | `/api/v1/chat/sessions` | Start a chat session |
| GET | `/api/v1/chat/sessions` | List sessions, most recently used first |
| GET | `/api/v1/chat/sessions/{id}/messages` | A session's messages, each assistant turn carrying its stored evidence chain |
| PATCH | `/api/v1/chat/sessions/{id}` | Rename a session |
| DELETE | `/api/v1/chat/sessions/{id}` | Delete a session and its messages |
| GET | `/api/v1/graph` | Get the entity/relationship graph (`label`, `max_depth`, `max_nodes` query params). Pass a `source:<name>` label to get one upload's subgraph |
| GET | `/api/v1/graph/sources` | Every `Source` node and nothing else — the hop view's landing state. Exactly `count(Source)` nodes, no edges |
| GET | `/api/v1/graph/expand` | One hop out from `node_id`: its immediate neighbours and the connecting edges, in both directions. Edge ids match `/api/v1/graph`'s, so results merge without duplicates |
| GET | `/health` | Health check |

---

## Prerequisites

- **Python 3.12** (a `.venv` already exists in `backend/`)
- **Node.js 18+** and npm
- A **Groq API key** ([free tier](https://console.groq.com/keys)) — optional, but
  recommended for speed/quality
- **Ollama** running locally (`ollama serve`) with a pulled model (e.g.
  `ollama pull llama3.2`) — required as a fallback if you don't set a Groq key, or
  if a Groq call fails

---

## Running the backend

```bash
cd backend

# Activate the existing virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (cmd):
.venv\Scripts\activate.bat
# macOS/Linux:
source .venv/bin/activate

# Install/update dependencies
pip install -r requirements.txt

# Configure environment (copy and edit as needed)
cp .env.example .env
```

Then edit `backend/.env`. Every setting is documented inline in
[`backend/.env.example`](backend/.env.example) — that file is the source of truth;
the ones you'll actually care about first:

```env
GROQ_API_KEY=            # leave blank to skip Groq and use Ollama only
OPENROUTER_API_KEY=      # answering fallback; blank falls through to Ollama
ZENROWS_API_KEY=         # blank falls back to trafilatura for URL scraping
OPIK_API_KEY=            # blank disables LLM tracing
EXTRACTION_BACKEND=gliner  # 'llm' for the per-chunk Groq/Ollama path
```

It runs with **all four keys blank** — embeddings, graph extraction, and
re-ranking are local; only answering needs an LLM, and Ollama covers that.

Start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

> **Upgrading from an earlier build?** Vectors now live in Qdrant, so the old
> `backend/storage/kb/vdb_*.json` files are dead weight. Delete
> `backend/storage/` and re-ingest once — nothing migrates them across.

The API is now live at `http://localhost:8000` (docs at `http://localhost:8000/docs`).
Data is persisted under `backend/storage/` (LightRAG's graph/vector/KV files plus the
`manifest.sqlite3` inventory), so restarting the server does not lose ingested data.

### LLM tracing (Opik)

If `OPIK_API_KEY` is set, every LLM call is traced to [Opik](https://www.comet.com/opik)
via `@opik.track` on `app/services/lightrag_engine.py`'s provider functions
(`llm_model_func` for entity/keyword extraction, `query_llm_func` for chat
answering, each nested under a Groq/OpenRouter/Ollama provider span) plus
`ingest_text` in `app/services/ingestion.py` as the parent trace for a
document's extraction calls. Leave `OPIK_API_KEY` blank to disable tracing —
nothing else changes. `OPIK_WORKSPACE` defaults to your account's default
workspace if unset.

---

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest app -q     # the whole backend suite

cd ../frontend
npm run lint && npx tsc --noEmit
npm test                                  # session grouping (node --test)
```

The depth harness is a plain generator, so the folder walker can be checked
against a tree that is provably deeper than any real one:

```bash
# 20 levels, each with a .py/.ts/.png/.bin and a node_modules beside it
python -m app.services.test_folder_ingest ./deep-tree 20
python -m scripts.verify_depth ./deep-tree/deeptree
```

`make_deep_tree()` in `app/services/test_folder_ingest.py` is what the depth,
zero-gap-chain and ignore-at-every-level tests all build on.

---

## Running the frontend

```bash
cd frontend

npm install

# Configure environment (copy and edit as needed)
cp .env.local.example .env.local
```

Edit `frontend/.env.local`:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Start the dev server:

```bash
npm run dev
```

Open `http://localhost:3000` — the public landing page. The app itself lives at
`/dashboard/knowledge` (the landing page's "Let's start" button goes there).

Other scripts: `npm run build` (production build), `npm run start` (serve production
build), `npm run lint` (ESLint).

---

## Running both together

1. Start Ollama if you're not using Groq: `ollama serve` (and make sure the model in
   `OLLAMA_MODEL` has been pulled).
2. Start the backend: `uvicorn app.main:app --reload --port 8000` (from `backend/`,
   venv activated).
3. Start the frontend: `npm run dev` (from `frontend/`).
4. Visit `http://localhost:3000`, upload/paste/link some content, and watch it
   appear in the **Inventory** table once processed.

---

## Chrome extension

`extension/` is a no-build Manifest V3 popup with two tabs: **Clip** (extract
the current page client-side with Defuddle, review the Markdown, then add it —
PDFs and YouTube URLs route to the backend parsers instead) and **Chat**
(stream an answer without leaving the tab you're on). Load it via
`chrome://extensions` → Developer mode → Load unpacked → select `extension/`.
See `extension/README.md` for details.

---

## Repository layout

```
nodeRels/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers: ingest.py, knowledge_base.py, chat.py, graph.py
│   │   ├── core/           # config.py — env-driven settings
│   │   ├── models/         # schemas.py — Pydantic request/response models
│   │   ├── services/
│   │   │   ├── lightrag_engine.py  # the single shared LightRAG instance
│   │   │   ├── qdrant_store.py     # hybrid dense+sparse Qdrant vector storage
│   │   │   ├── sparse.py           # BM25 sparse vectors (no model, no deps)
│   │   │   ├── graph_schema.py     # the property-graph ontology, enforced on write
│   │   │   ├── tabular_graph.py    # a workbook's structure as graph nodes/edges
│   │   │   ├── source_graph.py     # the Source supernode: one node per ingestion
│   │   │   ├── folder_ingest.py    # a directory tree mirrored into the graph
│   │   │   ├── code_intel.py       # classes/functions/calls (stdlib ast + tree-sitter)
│   │   │   ├── spreadsheet_query.py # NL -> validated DuckDB SQL -> exact rows
│   │   │   ├── ingestion.py        # dedup -> LightRAG insert -> manifest row
│   │   │   ├── dedup.py            # text normalization + SHA-256 hashing
│   │   │   ├── manifest.py         # SQLite document inventory
│   │   │   └── parsers/            # pdf.py, url.py, youtube.py, text.py, spreadsheet.py
│   │   └── main.py         # FastAPI app + CORS + lifespan (init/shutdown RAG)
│   ├── storage/             # qdrant/ vectors, kb/ graph+KV, spreadsheets.duckdb, manifest.sqlite3
│   ├── requirements.txt
│   ├── requirements-codeintel.txt  # optional: tree-sitter grammars, Pillow
│   ├── scripts/
│   │   ├── reingest_folders.py     # re-scan folder sources (manual, idempotent)
│   │   ├── verify_depth.py         # filesystem ground truth vs what landed in the graph
│   │   └── backfill_sources.py     # Source nodes for documents ingested before they existed
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── dashboard/
│   │   │   │   ├── knowledge/  # Knowledge Base page (upload, URL, paste, inventory)
│   │   │   │   ├── chat/       # Chat page — streaming Q&A over the knowledge base
│   │   │   │   └── graph/      # Graph Explorer page — 2D/3D force-directed view
│   │   │   └── page.tsx        # public landing page (hero + storage explainer)
│   │   ├── components/
│   │   │   ├── knowledge/      # Dropzone, UrlInput, PasteSandbox, InventoryTable, ...
│   │   │   ├── chat/           # ChatMessageBubble, DataResultTable (spreadsheet results)
│   │   │   ├── graph/           # GraphExplorer, ForceGraphCanvas, entity-colors
│   │   │   ├── landing/         # Hero (WebGPU shader + static fallback), HowItWorks
│   │   │   └── ui/              # shadcn primitives (button, card, table, ...)
│   │   ├── lib/api.ts          # typed fetch wrappers around the backend API
│   │   └── store/knowledge-store.ts  # Zustand store for the document list
│   └── .env.local.example
├── extension/                # Chrome extension (Manifest V3, no build step)
│   ├── manifest.json
│   ├── popup.html / popup.css / popup.js
│   ├── api.js                # fetch wrappers (mirrors frontend/src/lib/api.ts)
│   └── markdown-lite.js       # tiny markdown renderer for the chat popup
├── scripts/gen_logo_assets.py  # regenerates the extension icons from logo.png
├── lightrag_hybrid/          # early standalone prototype, superseded by backend/ — kept for reference, not wired into the app
└── rag-fullstack-toolkit/   # Claude Code plugin/skills used to scaffold this project
```

---

## License

[MIT](LICENSE)
