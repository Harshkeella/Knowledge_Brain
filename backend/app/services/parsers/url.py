"""URL article ingestion: ZenRows markdown scrape first, trafilatura as fallback.

Every successful scrape is cached as a .md file with YAML frontmatter under
SCRAPED_ARTICLES_DIR, so re-ingesting the same URL never costs a second ZenRows
credit (and the raw scrape stays human-readable / re-indexable).
"""

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
import trafilatura
import yaml

from app.core.config import get_settings

logger = logging.getLogger("app.parsers.url")
_settings = get_settings()

ZENROWS_ENDPOINT = "https://api.zenrows.com/v1/"

_YOUTUBE_HOST_RE = re.compile(r"(?:youtube\.com|youtu\.be)", re.IGNORECASE)
# Below this, treat the scrape as blocked/JS-gated rather than a real article.
_MIN_BODY_CHARS = 200
_SOURCE_TYPE = {"zenrows": "article_zenrows", "trafilatura": "article"}

# Everything from the first of these markers to the end of the document is
# trailing site cruft (author bio, share buttons, related feed, references) —
# never article body. Cutting it is pure token savings before the LLM ever
# sees the text.
_TRAILING_CUTOFF = re.compile(
    r"^(?:#{1,3}\s*)?(written by|share this article|related articles?|"
    r"references|see also|external links|further reading|"
    r"notes and references|footnotes|citations)\b.*",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
# Bare footnote markers left behind once links are stripped: " [1]", "[23]".
_FOOTNOTE_MARKER = re.compile(r"\s?\[\d{1,3}\]")


def is_youtube_url(url: str) -> bool:
    return bool(_YOUTUBE_HOST_RE.search(url))


def _normalize(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, "")
    )


def _cache_path(url: str) -> str:
    digest = hashlib.sha256(_normalize(url).encode()).hexdigest()[:16]
    return os.path.join(_settings.scraped_articles_dir, f"{digest}.md")


def _read_cache(path: str) -> tuple[str, str | None, str] | None:
    """Returns (text, title, source_type) from a cached .md, or None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        parts = f.read().split("---\n", 2)
    if len(parts) != 3:
        return None
    meta = yaml.safe_load(parts[1]) or {}
    return (
        parts[2].strip(),
        meta.get("title"),
        _SOURCE_TYPE.get(meta.get("source"), "article"),
    )


def _write_cache(path: str, url: str, text: str, metadata: dict, source: str) -> None:
    frontmatter = yaml.safe_dump(
        {
            "url": url,
            **metadata,  # title/author/date/sitename/description/categories/tags
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "word_count": len(text.split()),
        },
        sort_keys=False,
        allow_unicode=True,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\n{frontmatter}---\n\n{text}\n")


async def _zenrows_get(url: str, js_render: bool, premium_proxy: bool) -> str:
    # No response_type: ZenRows returns raw HTML and trafilatura does the
    # main-content extraction, which drops nav/sidebar/footer that ZenRows'
    # own markdown conversion keeps. Less junk text = fewer LLM tokens.
    params = {"url": url, "apikey": _settings.zenrows_api_key}
    if js_render:
        params["js_render"] = "true"
    if premium_proxy:
        params["premium_proxy"] = "true"

    # ponytail: client per call; app-lifespan singleton if scrape volume grows.
    async with httpx.AsyncClient(timeout=_settings.zenrows_timeout_seconds) as client:
        resp = await client.get(ZENROWS_ENDPOINT, params=params)
    resp.raise_for_status()
    logger.info(
        "ZenRows scraped %s (js_render=%s premium_proxy=%s cost=%s)",
        url,
        js_render,
        premium_proxy,
        resp.headers.get("X-Request-Cost"),
    )
    return resp.text.strip()


async def _scrape_zenrows(url: str) -> str | None:
    """Cheap attempt first, then at most one retry with js_render+premium_proxy
    (both cost extra credits). None means "fall back to a direct fetch"."""
    if not _settings.zenrows_api_key:
        return None

    attempts = [
        (_settings.zenrows_js_render_default, _settings.zenrows_premium_proxy_default)
    ]
    if attempts[0] != (True, True):
        attempts.append((True, True))

    for js_render, premium_proxy in attempts:
        try:
            html = await _zenrows_get(url, js_render, premium_proxy)
        except Exception as e:
            logger.warning("ZenRows failed for %s (%s)", url, e)
            continue
        if len(html) >= _MIN_BODY_CHARS:
            return html
        logger.warning("ZenRows returned %d chars for %s", len(html), url)
    return None


def _extract_markdown(html: str, url: str) -> tuple[str, dict]:
    """HTML -> (clean article markdown, page-declared metadata). Rule-based
    only: no LLM touches the article text anywhere in this path."""
    body = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=False,
        include_images=True,
        include_tables=True,
        include_formatting=True,
        favor_precision=True,
        # No deduplicate=True: its "already seen this text" cache is per-process,
        # so in a long-running server a re-scrape returns an empty body.
    )
    if not body or not body.strip():
        raise ValueError(f"No article content extracted from: {url}")

    meta = trafilatura.extract_metadata(html)

    # Precision mode can drop a <header> block that holds the title/dek, so
    # re-attach them from the page's own og: metadata when they're missing.
    if meta and meta.title and meta.title.strip() not in body[:300]:
        prefix = [f"# {meta.title.strip()}"]
        if meta.description and meta.description.strip() not in body[:500]:
            prefix.append(meta.description.strip())
        body = "\n\n".join(prefix) + "\n\n" + body

    body = _FOOTNOTE_MARKER.sub("", _TRAILING_CUTOFF.sub("", body))

    fields = ("title", "author", "date", "sitename", "description", "categories", "tags")
    metadata = {
        f: getattr(meta, f) for f in fields if meta and getattr(meta, f, None)
    }
    return body.strip(), metadata


def _fetch_and_extract(url: str) -> tuple[str, dict]:
    """Direct fetch fallback, same extractor — sync, so callers thread it."""
    html = trafilatura.fetch_url(url)
    if not html:
        raise ValueError(f"Could not fetch URL: {url}")
    return _extract_markdown(html, url)


async def extract_article(url: str) -> tuple[str, str | None, str]:
    """Returns (markdown, title, source_type)."""
    path = _cache_path(url)
    cached = _read_cache(path)
    if cached:
        logger.info("Serving %s from scrape cache", url)
        return cached

    html = await _scrape_zenrows(url)
    if html:
        text, metadata = await asyncio.to_thread(_extract_markdown, html, url)
        source = "zenrows"
    else:
        # trafilatura's fetch + extract are sync — keep them off the event loop.
        text, metadata = await asyncio.to_thread(_fetch_and_extract, url)
        source = "trafilatura"

    _write_cache(path, url, text, metadata, source)
    return text, metadata.get("title"), _SOURCE_TYPE[source]
