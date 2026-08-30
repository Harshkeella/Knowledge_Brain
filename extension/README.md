# nodeRels — Knowledge Base Clipper (Chrome extension)

A Manifest V3 popup with two tabs:

- **Clip** — extract the current page's article *in the browser* (Defuddle,
  the same engine Obsidian Web Clipper uses), show you the resulting Markdown
  for review/editing, and only then add it (`POST /api/v1/ingest/text` with
  `source_type: "article_clipper"`). You can also add just the text you've
  selected on the page.
- **Chat** — ask the knowledge base a question and watch the answer stream in
  (`POST /api/v1/chat/stream`), same as the dashboard's Chat page but without
  leaving the current tab.

No build step — plain HTML/CSS/JS (ES modules), loaded straight from this
folder. `vendor/defuddle.js` is the prebuilt UMD bundle from the
[defuddle](https://www.npmjs.com/package/defuddle) npm package (MIT, see
`vendor/LICENSE-defuddle`), copied in as-is rather than bundled, so there is
still nothing to run before loading the extension.

## How clipping works

1. **YouTube** → the URL goes to the backend (`ingest/url` →
   `youtube-transcript-api`).
2. **PDF** → the bytes are fetched and posted to `ingest/file` → PyMuPDF.
3. **Anything else** → `vendor/defuddle.js` + `extract.js` are injected into
   the tab and run against the live DOM, so logged-in, paywalled-but-visible
   and JS-rendered pages extract correctly where a server-side fetch wouldn't.
   Defuddle's `markdown` option emits Markdown directly — no Turndown needed.

The extracted title, source and Markdown appear in a **preview** panel. It is
read-only until you press **Edit**; nothing is sent anywhere until you press
**Add to Knowledge Base**. If extraction comes back empty or too short (an
app shell that hasn't rendered yet, say), the popup says so and points you at
**Add selected text** instead of submitting a garbage clip.

The dashboard's "Add from URL" flow (ZenRows → trafilatura, server-side) is
unchanged and unaffected.

### Per-site extractors

Defuddle ships its own extractors for the usual awkward pages (ChatGPT,
Claude, GitHub, Hacker News, Bluesky…). `NODE_RELS_EXTRACTORS` at the top of
`extract.js` is the local override point: map a domain to
`(document) => ({ title, markdown })` when a specific site extracts badly.

## Tests

```
cd tests && npm install && npm test
```

Runs `vendor/defuddle.js` + `extract.js` — the exact shipped code — over the
saved page fixtures in `tests/fixtures/` under jsdom, asserting title,
metadata and non-empty Markdown come back for a standard article, a
subscriber-visible paywalled article, and an SPA-style app shell. `jsdom` is
a test-only dependency; the extension itself still has none.

## Load it

1. Make sure the backend is running (`cd backend && uvicorn app.main:app --port 8000`).
2. Open `chrome://extensions`, enable **Developer mode** (top right).
3. Click **Load unpacked** and select this `extension/` folder.
4. Pin the extension (puzzle-piece icon → pin) for easy access.

## Backend URL

Defaults to `http://127.0.0.1:8000`. If your backend runs elsewhere, click the
⚙ in the popup header and change it — it's saved via `chrome.storage.local`.

The manifest's `host_permissions` covers `127.0.0.1:8000` and `localhost:8000`
so the extension can call the backend without the backend needing any CORS
changes. If you point it at a different host/port, add that origin to
`host_permissions` in `manifest.json` and reload the extension.

## Notes / limits

- Chat history is kept only for the popup's lifetime (it unmounts when the
  popup closes) — not persisted like the dashboard's chat.
- "Add selected text" reads `window.getSelection()` from the active tab via
  `chrome.scripting.executeScript` — it needs an actual text selection on the
  page first.
- Only `http(s)://` pages can be clipped (not `chrome://`, `file://`, etc.).
- PDF and YouTube detection is by URL shape (`*.pdf`, youtube.com/youtu.be) —
  a PDF served from an extensionless URL falls through to the DOM extractor.
