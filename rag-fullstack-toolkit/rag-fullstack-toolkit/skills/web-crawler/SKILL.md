---
name: web-crawler
description: Crawl web pages and articles into clean markdown/JSON for RAG ingestion, using Playwright for JS-rendered pages and a lightweight static path for plain HTML. Use when the user asks to add a new source to crawl, debug a scraper returning empty/garbled content, handle pagination or rate limiting, or define the output schema for the ingestion pipeline.
---

# Web crawler for RAG ingestion

## Decide: static or dynamic

Try the static path first — it's 10x faster and avoids browser overhead:

```python
import httpx
from selectolax.parser import HTMLParser

def fetch_static(url: str) -> str | None:
    resp = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200:
        return None
    tree = HTMLParser(resp.text)
    # Strip nav/footer/script noise before extracting
    for tag in tree.css("script, style, nav, footer, header, aside"):
        tag.decompose()
    return tree.body.text(separator="\n", strip=True) if tree.body else None
```

If the page needs JS to render content (check: does `httpx` output look empty or missing the main content vs what you see in a browser?), fall back to Playwright — same pattern as KG Capture's pipeline:

```python
from playwright.async_api import async_playwright

async def fetch_dynamic(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=20000)
        content = await page.locator("body").inner_text()
        await browser.close()
        return content
```

## Output schema

Every crawled document should normalize to this shape before it hits the chunking step — keeping this consistent is what lets the ingestion and query skills stay decoupled from crawler internals:

```json
{
  "url": "https://example.com/article",
  "title": "Article title",
  "content": "Cleaned plain text or markdown",
  "crawled_at": "2026-07-24T00:00:00Z",
  "content_hash": "sha256 of content, for dedup"
}
```

## Dedup and re-crawl handling

Hash the cleaned content (not the raw HTML — whitespace/ad noise changes trigger false re-ingests). Before writing to Neo4j, check if a node with that `content_hash` already exists; skip re-embedding if so, but always update `crawled_at` so stale-content queries can filter correctly.

```python
import hashlib
content_hash = hashlib.sha256(content.encode()).hexdigest()
```

## Rate limiting and politeness

- Respect `robots.txt` — check before crawling a new domain.
- Add a delay between requests to the same domain (start at 1-2s, back off on 429s).
- Batch crawl jobs with `asyncio.Semaphore` (concurrency of 3-5 is usually safe) rather than firing all requests at once.

## Common pitfalls

- **Windows path separators** if you're writing crawled output to disk before ingestion — use `pathlib.Path`, never string-concatenate paths (this bit you in RepoGraph; same fix applies here).
- **Silent Playwright timeouts** on infinite-scroll pages — set an explicit `wait_until` strategy and a max scroll count, don't loop indefinitely.
- **Encoding mismatches** — always decode as UTF-8 explicitly; some sites lie about their charset header.
