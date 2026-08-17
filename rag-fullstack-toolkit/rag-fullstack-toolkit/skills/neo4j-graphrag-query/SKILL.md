---
name: neo4j-graphrag-query
description: Set up and query a Neo4j vector index combined with graph traversal for GraphRAG-style retrieval. Use when the user asks to create the vector index, debug retrieval returning irrelevant chunks, expand results via related entities, or tune the hybrid vector+graph query.
---

# Neo4j GraphRAG: vector index + graph expansion

## One-time setup: create the vector index

```cypher
CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS {indexConfig: {
  `vector.dimensions`: 1024,
  `vector.similarity_function`: 'cosine'
}}
```

Match `vector.dimensions` exactly to your embedding model's output size (see the rag-ingest-embed skill — 1024 for voyage-3). Verify the index came up correctly:

```cypher
SHOW VECTOR INDEXES
```

## Writing chunks with embeddings

```cypher
UNWIND $chunks AS chunk
MERGE (c:Chunk {chunk_id: chunk.chunk_id})
SET c.text = chunk.text,
    c.embedding = chunk.embedding,
    c.source_url = chunk.source_url,
    c.embedded_at = datetime()
WITH c, chunk
MATCH (d:Document {url: chunk.source_url})
MERGE (d)-[:HAS_CHUNK]->(c)
```

## Step 1: pure vector similarity search

```cypher
CALL db.index.vector.queryNodes('chunk_embeddings', $k, $query_embedding)
YIELD node AS chunk, score
RETURN chunk.text AS text, chunk.source_url AS source, score
ORDER BY score DESC
```

## Step 2: graph expansion (the "graph" in GraphRAG)

This is what a pure vector store can't do: use entity relationships to pull in context the similarity search alone would miss — e.g. other chunks that mention the same entity as a top result.

```cypher
CALL db.index.vector.queryNodes('chunk_embeddings', $k, $query_embedding)
YIELD node AS chunk, score
OPTIONAL MATCH (chunk)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(related:Chunk)
WHERE related <> chunk
WITH chunk, score, collect(DISTINCT related.text)[..3] AS related_context
RETURN chunk.text AS text, chunk.source_url AS source, score, related_context
ORDER BY score DESC
```

Only add this expansion step when the query genuinely needs relational context (e.g. "how does X relate to Y" style questions). For simple factual lookups, pure vector search is faster and just as accurate — don't pay the graph-traversal cost by default.

## Reranking before sending to Claude

Vector similarity alone often surfaces chunks that are topically close but not actually answering the question. A cheap rerank pass: ask Claude (or a smaller/faster call) to score each candidate 0-10 for relevance to the query, then keep only the top 3-5 before building the final context — this matters more for answer quality than almost any other tuning knob in the pipeline.

## Common pitfalls

- Forgetting `OPTIONAL MATCH` for graph expansion — a plain `MATCH` silently drops chunks that have no entity relationships yet, which quietly degrades results as your graph fills in unevenly across documents.
- Returning too many chunks into the prompt "just in case" — this dilutes Claude's attention and increases cost/latency without improving answers. Cap at 5-8 chunks post-rerank.
- Stale embeddings after a re-crawl updates a document's text but the chunk nodes keep old vectors — always re-embed and re-write, don't just update the `text` field.
