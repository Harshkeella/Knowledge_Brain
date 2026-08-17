"""URL scrape path: ZenRows raw HTML -> rule-based article extraction (no LLM),
direct-fetch fallback, and a .md cache in front of both."""

import asyncio

import httpx
import pytest

from app.services.parsers import url as u

_BODY = " ".join(
    f"Sentence {i} explains how the enterprise adopted the new platform "
    "across several teams and what changed as a result."
    for i in range(12)
)
ARTICLE_HTML = f"""<!doctype html>
<html><head>
  <meta property="og:title" content="Real Title" />
  <meta property="og:description" content="A short dek about the article." />
  <meta property="og:site_name" content="Test Site" />
</head><body>
  <nav><a href="/">Home</a><a href="/about">About</a><a href="/jobs">Careers</a></nav>
  <article>
    <p>{_BODY}</p>
    <p>{_BODY} Footnote follows [1].</p>
    <h2>Related Articles</h2>
    <p>Some other article you should read instead of this one entirely.</p>
  </article>
  <footer>Copyright 2026 Test Site. All rights reserved.</footer>
</body></html>"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(u._settings, "scraped_articles_dir", str(tmp_path))
    monkeypatch.setattr(u._settings, "zenrows_api_key", "test-key")
    monkeypatch.setattr(u._settings, "zenrows_js_render_default", False)
    monkeypatch.setattr(u._settings, "zenrows_premium_proxy_default", False)


def _mock_zenrows(monkeypatch, *responses):
    """Each response: a string body, or an Exception to raise. Records params."""
    calls = []

    async def fake_get(self, endpoint, params=None):
        calls.append(params)
        result = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return httpx.Response(200, text=result, request=httpx.Request("GET", endpoint))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    return calls


def test_zenrows_html_is_cleaned_not_taken_verbatim(monkeypatch, tmp_path):
    calls = _mock_zenrows(monkeypatch, ARTICLE_HTML)

    text, title, source_type = asyncio.run(u.extract_article("https://a.test/post"))

    assert title == "Real Title"
    assert source_type == "article_zenrows"
    assert len(calls) == 1
    # Raw HTML request: no response_type, no paid render flags on attempt 1.
    assert "response_type" not in calls[0] and "js_render" not in calls[0]
    # Boilerplate and footnote markers are gone before the LLM ever sees it.
    assert "Careers" not in text and "All rights reserved" not in text
    assert "Related Articles" not in text and "[1]" not in text
    assert "Sentence 3 explains" in text

    cached = list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")
    assert cached.startswith("---\n")
    assert "source: zenrows" in cached and "sitename: Test Site" in cached


def test_short_body_retries_with_js_render(monkeypatch):
    calls = _mock_zenrows(monkeypatch, "too short", ARTICLE_HTML)

    _, _, source_type = asyncio.run(u.extract_article("https://a.test/js"))

    assert source_type == "article_zenrows"
    assert len(calls) == 2
    assert calls[1]["js_render"] == "true"
    assert calls[1]["premium_proxy"] == "true"


def test_timeout_falls_back_to_direct_fetch(monkeypatch):
    calls = _mock_zenrows(monkeypatch, httpx.TimeoutException("boom"))
    monkeypatch.setattr(u.trafilatura, "fetch_url", lambda url: ARTICLE_HTML)

    text, title, source_type = asyncio.run(u.extract_article("https://a.test/slow"))

    assert (title, source_type) == ("Real Title", "article")
    assert "Sentence 3 explains" in text
    assert len(calls) == 2  # cheap attempt + one retry, never more


def test_cache_hit_skips_the_network(monkeypatch):
    _mock_zenrows(monkeypatch, ARTICLE_HTML)
    asyncio.run(u.extract_article("https://a.test/cached"))

    def explode(*args, **kwargs):
        raise AssertionError("cache hit must not hit the network")

    monkeypatch.setattr(httpx.AsyncClient, "get", explode)
    # Same URL, trailing slash + fragment -- normalization must still hit cache.
    text, title, source_type = asyncio.run(
        u.extract_article("https://a.test/cached/#top")
    )
    assert (title, source_type) == ("Real Title", "article_zenrows")
    assert "Sentence 3 explains" in text
