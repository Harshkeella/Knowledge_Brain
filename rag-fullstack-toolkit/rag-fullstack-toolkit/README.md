# rag-fullstack-toolkit

A Claude Code plugin bundling five skills for building a RAG chatbot with web crawling,
Neo4j GraphRAG retrieval, a FastAPI streaming backend, and a React chat UI.

## Skills included

| Skill | Fires when you... |
|---|---|
| `web-crawler` | add a crawl source, debug a scraper, handle pagination/rate limits |
| `rag-ingest-embed` | tune chunk size, fix bad retrieval from chunking, switch embedding models |
| `neo4j-graphrag-query` | set up the vector index, debug irrelevant retrieval, tune graph expansion |
| `fastapi-chat-backend` | wire up the chat endpoint, debug streaming issues, add citations |
| `react-chat-ui` | build/fix the chat UI, debug choppy streaming, add a sources panel |

## Install locally (no marketplace needed)

From the directory containing this folder:

```bash
claude plugin install ./rag-fullstack-toolkit
```

Or interactively from inside a Claude Code session:

```
/plugin install ./rag-fullstack-toolkit
```

Then verify:

```
/plugin list
```

You should see `rag-fullstack-toolkit` listed with its 5 skills registered. Claude will
load each skill automatically when a task matches its description — you don't invoke
them by name unless you want to force one explicitly (e.g. "use the neo4j-graphrag-query
skill to debug this").

## If you want to share it later (optional)

Push this folder to a GitHub repo, add a `.claude-plugin/marketplace.json` at the repo
root pointing to it, and others can install with:

```
/plugin marketplace add yourusername/your-repo-name
/plugin install rag-fullstack-toolkit
```

## Editing a skill

Just edit the `SKILL.md` file directly — Claude Code watches the plugin's skill
directories and picks up changes without a restart. Keep the `description` field
specific: it's the trigger Claude uses to decide when to load the rest of the file,
not a summary for humans.
