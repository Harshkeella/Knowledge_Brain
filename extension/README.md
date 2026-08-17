# Crag — Knowledge Base Clipper (Chrome extension)

A Manifest V3 popup with two tabs:

- **Clip** — add the current page's URL, or just the text you've selected on
  the page, to the knowledge base (`POST /api/v1/ingest/url` /
  `/api/v1/ingest/text`).
- **Chat** — ask the knowledge base a question and watch the answer stream in
  (`POST /api/v1/chat/stream`), same as the dashboard's Chat page but without
  leaving the current tab.

No build step — plain HTML/CSS/JS (ES modules), loaded straight from this
folder.

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
- Only `http(s)://` pages can be clipped (not `chrome://`, PDFs viewed in the
  built-in viewer, etc.).
- No icons are bundled yet — Chrome shows a default placeholder in the
  toolbar. Drop `icon16.png` / `icon48.png` / `icon128.png` into this folder
  and add an `"icons"` entry to `manifest.json` if you want a custom one.
