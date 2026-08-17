---
name: rag-ingest-embed
description: Chunk crawled documents along semantic boundaries and embed them for storage in the vector index. Use when the user asks to tune chunk size, fix bad retrieval caused by poor chunking, add a new embedding provider, or debug embedding dimension mismatches.
---

# Chunking and embedding for RAG

## Chunk by structure, not fixed windows

Fixed character/token windows cut sentences and entities in half, which hurts retrieval precision. Use spaCy to split on sentence boundaries first, then group sentences into chunks up to a target size:

```python
import spacy

nlp = spacy.load("en_core_web_sm")

def chunk_document(text: str, target_chars: int = 800, overlap_sentences: int = 1) -> list[str]:
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]

    chunks, current, current_len = [], [], 0
    for sent in sentences:
        if current_len + len(sent) > target_chars and current:
            chunks.append(" ".join(current))
            # carry the last N sentences forward for context continuity
            current = current[-overlap_sentences:]
            current_len = sum(len(s) for s in current)
        current.append(sent)
        current_len += len(sent)
    if current:
        chunks.append(" ".join(current))
    return chunks
```

Target 600-1000 characters per chunk for prose. Go smaller (~300-500) for dense technical/reference content where precision matters more than context.

## Embedding

Batch calls — never embed one chunk at a time, it's needlessly slow and costly:

```python
import voyageai

vo = voyageai.Client()

def embed_chunks(chunks: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(chunks, model="voyage-3", input_type=input_type)
    return result.embeddings
```

Use `input_type="document"` when embedding chunks for storage, and `input_type="query"` when embedding the user's question at retrieval time — Voyage's asymmetric models expect this distinction and retrieval quality drops without it.

## Dimension consistency — the #1 cause of silent failures

Whatever embedding model you pick, the vector index dimension in Neo4j must match it exactly. `voyage-3` outputs 1024-dim vectors. If you switch models later, you must rebuild the index and re-embed everything — there's no in-place migration.

## Metadata to carry alongside each chunk

Store these fields with every chunk node so retrieval can filter and cite properly:

```json
{
  "chunk_id": "uuid",
  "source_url": "https://...",
  "source_title": "...",
  "chunk_index": 0,
  "content_hash": "from the crawler skill",
  "embedded_at": "2026-07-24T00:00:00Z"
}
```

## Common pitfalls

- Re-embedding unchanged content on every crawl — check `content_hash` from the crawler skill before embedding.
- Chunking mid-sentence when a document has no clear sentence boundaries (e.g. code blocks, tables) — detect these and chunk by logical unit (function, row) instead of running spaCy sentence splitting on them.
