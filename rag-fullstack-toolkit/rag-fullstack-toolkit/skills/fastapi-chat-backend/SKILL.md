---
name: fastapi-chat-backend
description: Build a FastAPI backend that retrieves context from the RAG store and streams Claude's response via SSE to the chat UI. Use when the user asks to wire up the chat endpoint, debug streaming that cuts off or buffers, add source citations to the stream, or handle concurrent chat sessions.
---

# FastAPI RAG chat backend with SSE streaming

## Endpoint shape

Stream two kinds of events: retrieved sources first (so the UI can show "searching..." then sources), then the answer tokens as Claude generates them.

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import anthropic
import json

app = FastAPI()
client = anthropic.Anthropic()

async def rag_stream(query: str):
    query_embedding = embed_query(query)          # from rag-ingest-embed skill
    chunks = retrieve_and_rerank(query_embedding)  # from neo4j-graphrag-query skill

    # Emit sources first
    yield f"data: {json.dumps({'type': 'sources', 'sources': chunks})}\n\n"

    context = "\n\n".join(c["text"] for c in chunks)
    prompt = f"Context:\n{context}\n\nQuestion: {query}\n\nAnswer using only the context above. Cite sources by URL."

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"

@app.get("/chat/stream")
async def chat_stream(q: str):
    return StreamingResponse(rag_stream(q), media_type="text/event-stream")
```

## Why sources-first matters

Sending sources before the answer lets the UI render "5 sources found" or a citations panel immediately, rather than the user staring at a blank screen until the first token arrives — retrieval + rerank often takes longer than the time to first token.

## Handling concurrent sessions

Don't hold conversation state in a global variable — it'll leak across concurrent requests. Keep session state in a store keyed by session ID (Redis for production, an in-memory dict keyed by UUID is fine for local dev), and pass the running message history into each `messages.stream()` call explicitly.

## Common pitfalls

- **Buffering instead of streaming**: if you're behind nginx or a reverse proxy, disable buffering for the SSE route (`X-Accel-Buffering: no` header) or tokens will arrive in one lump instead of streaming.
- **Dropped connections on long answers**: set a generous timeout on both the FastAPI server and any proxy in front of it — default timeouts (30s) will cut off a slow generation mid-stream.
- **CORS**: SSE from a different origin (React dev server on a different port) needs `Access-Control-Allow-Origin` set explicitly; `StreamingResponse` doesn't get CORS middleware applied automatically in all FastAPI versions — verify it's actually present in the response headers, not just configured on the app.
- **JSON-encoding newlines badly**: always `json.dumps` the payload before interpolating into the `data:` line — raw text with newlines will break the SSE frame format.
