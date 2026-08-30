import { getBackendUrl, setBackendUrl, ingestUrl, ingestText, ingestPdfUrl, streamChat } from "./api.js";
import {
  authConfigured,
  currentEmail,
  getAccessToken,
  getAuthConfig,
  setAuthConfig,
  signIn,
  signOut,
} from "./auth.js";
import { renderMarkdownLite } from "./markdown-lite.js";

// --- Tabs ---------------------------------------------------------------

const tabButtons = document.querySelectorAll(".tab-btn");
const panels = {
  clip: document.getElementById("panel-clip"),
  preview: document.getElementById("panel-preview"),
  chat: document.getElementById("panel-chat"),
  signin: document.getElementById("panel-signin"),
};

function showPanel(name) {
  for (const [key, panel] of Object.entries(panels)) panel.classList.toggle("hidden", key !== name);
}

for (const btn of tabButtons) {
  btn.addEventListener("click", () => {
    for (const b of tabButtons) {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", String(b === btn));
    }
    showPanel(btn.dataset.tab);
  });
}

// --- Settings -------------------------------------------------------------

const settingsToggle = document.getElementById("settings-toggle");
const settingsPanel = document.getElementById("settings-panel");
const backendUrlInput = document.getElementById("backend-url");
const settingsSave = document.getElementById("settings-save");
const settingsStatus = document.getElementById("settings-status");
const authUrlInput = document.getElementById("auth-url");
const authKeyInput = document.getElementById("auth-key");

settingsToggle.addEventListener("click", async () => {
  settingsPanel.classList.toggle("hidden");
  if (!settingsPanel.classList.contains("hidden")) {
    backendUrlInput.value = await getBackendUrl();
    const { url, anonKey } = await getAuthConfig();
    authUrlInput.value = url;
    authKeyInput.value = anonKey;
  }
});

settingsSave.addEventListener("click", async () => {
  const url = backendUrlInput.value.trim().replace(/\/$/, "");
  if (!url) return;
  await setBackendUrl(url);
  await setAuthConfig(authUrlInput.value, authKeyInput.value);
  // A hosted backend is on an origin the manifest cannot know, so ask for it
  // now rather than failing every later fetch with an opaque network error.
  if (!(await requestOrigins([url, authUrlInput.value.trim()]))) {
    settingsStatus.textContent =
      "Saved, but access to those addresses was not granted.";
    return;
  }
  settingsStatus.textContent = "Saved.";
  setTimeout(() => (settingsStatus.textContent = ""), 1500);
  await refreshAuthUi();
});

/** Ask for access to the origins the user just configured. Localhost is
 *  already in the manifest, so a local setup never sees a prompt. */
async function requestOrigins(urls) {
  const origins = [];
  for (const value of urls) {
    if (!value) continue;
    try {
      const { origin } = new URL(value);
      if (!origin.startsWith("http://localhost") && !origin.startsWith("http://127.")) {
        origins.push(`${origin}/*`);
      }
    } catch {
      // Not a URL the browser can grant; the field's own validation says so.
    }
  }
  if (!origins.length) return true;
  if (await chrome.permissions.contains({ origins })) return true;
  return chrome.permissions.request({ origins });
}

// --- Sign in --------------------------------------------------------------

const tabsEl = document.querySelector(".tabs");
const signInForm = document.getElementById("signin-form");
const signInEmail = document.getElementById("signin-email");
const signInPassword = document.getElementById("signin-password");
const signInStatus = document.getElementById("signin-status");
const signOutBtn = document.getElementById("sign-out");

/**
 * Show the clipper or the sign-in form, depending on whether there is a usable
 * token. Called at load and after every auth change -- the popup is recreated
 * on each open, so this runs far more often than a page would.
 */
async function refreshAuthUi() {
  if (!(await authConfigured())) {
    // No project configured: a local single-user backend, nothing to sign in to.
    signOutBtn.classList.add("hidden");
    return true;
  }

  let token = null;
  try {
    token = await getAccessToken();
  } catch {
    token = null;
  }

  const signedIn = Boolean(token);
  signOutBtn.classList.toggle("hidden", !signedIn);
  tabsEl.classList.toggle("hidden", !signedIn);
  if (signedIn) {
    signOutBtn.title = `Sign out (${(await currentEmail()) ?? ""})`.trim();
  } else {
    showPanel("signin");
  }
  return signedIn;
}

signInForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  signInStatus.textContent = "Signing in…";
  try {
    await signIn(signInEmail.value, signInPassword.value);
    signInPassword.value = "";
    signInStatus.textContent = "";
    await refreshAuthUi();
    showPanel("clip");
  } catch (e) {
    signInStatus.textContent = e.message;
  }
});

signOutBtn.addEventListener("click", async () => {
  await signOut();
  await refreshAuthUi();
});

refreshAuthUi();

// --- Clip -----------------------------------------------------------------

const pageTitleEl = document.getElementById("page-title");
const pageUrlEl = document.getElementById("page-url");
const clipPageBtn = document.getElementById("clip-page");
const clipSelectionBtn = document.getElementById("clip-selection");
const clipStatus = document.getElementById("clip-status");

// ponytail: URL-shape checks, not content sniffing — a PDF served from an
// extensionless URL still falls through to the DOM extractor. Sniff the
// Content-Type here if that shows up in practice.
const PDF_PATH_RE = /\.pdf(?:$|[?#])/i;
const YOUTUBE_HOST_RE = /(?:^|\.)(?:youtube\.com|youtu\.be)$/i;
const MIN_CONTENT_CHARS = 200;
const SELECTION_HINT = 'Select the text you want on the page, then use "Add selected text".';

let activeTab = null;

async function loadActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTab = tab;
  if (!tab || !tab.url || !/^https?:\/\//.test(tab.url)) {
    pageTitleEl.textContent = "No page to clip";
    pageUrlEl.textContent = tab?.url ?? "";
    clipPageBtn.disabled = true;
    clipSelectionBtn.disabled = true;
    return;
  }
  pageTitleEl.textContent = tab.title || tab.url;
  pageUrlEl.textContent = tab.url;
}

function setClipStatus(text, kind) {
  clipStatus.textContent = text;
  clipStatus.className = "status" + (kind ? ` ${kind}` : "");
}

function describeIngestResult(result) {
  if (result.deduped) return `Already in the knowledge base (${result.file_name}).`;
  return `Added "${result.file_name}" — ${result.chunk_count} chunk${result.chunk_count === 1 ? "" : "s"}.`;
}

function errorText(e) {
  return e instanceof Error ? e.message : String(e);
}

// Extraction runs in the tab, against the DOM the user is actually looking at,
// so logged-in and JS-rendered pages work where a server-side fetch wouldn't.
async function extractPage(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ["vendor/defuddle.js", "extract.js"],
  });
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => window.__nodeRelsExtract(),
  });
  return result;
}

clipPageBtn.addEventListener("click", async () => {
  if (!activeTab?.url) return;
  const { hostname, pathname } = new URL(activeTab.url);
  clipPageBtn.disabled = true;
  try {
    // PDFs and YouTube go to the backend parsers (PyMuPDF /
    // youtube-transcript-api), which handle them better than any DOM pass.
    if (YOUTUBE_HOST_RE.test(hostname)) {
      setClipStatus("Fetching the transcript…");
      setClipStatus(describeIngestResult(await ingestUrl(activeTab.url)), "success");
      return;
    }
    if (PDF_PATH_RE.test(pathname)) {
      setClipStatus("Sending the PDF to the backend…");
      setClipStatus(describeIngestResult(await ingestPdfUrl(activeTab.url)), "success");
      return;
    }

    setClipStatus("Extracting article…");
    const extracted = await extractPage(activeTab.id);
    if (!extracted?.markdown || extracted.markdown.length < MIN_CONTENT_CHARS) {
      setClipStatus(`Couldn't find article content on this page. ${SELECTION_HINT}`, "error");
      return;
    }
    setClipStatus("");
    openPreview(extracted);
  } catch (e) {
    setClipStatus(errorText(e), "error");
  } finally {
    clipPageBtn.disabled = false;
  }
});

clipSelectionBtn.addEventListener("click", async () => {
  if (!activeTab?.id) return;
  clipSelectionBtn.disabled = true;
  setClipStatus("Reading selection…");
  try {
    const [{ result: selection } = {}] = await chrome.scripting.executeScript({
      target: { tabId: activeTab.id },
      func: () => window.getSelection()?.toString() ?? "",
    });
    const text = (selection || "").trim();
    if (!text) {
      setClipStatus("No text selected on the page.", "error");
      return;
    }
    setClipStatus("Adding selection…");
    const result = await ingestText(text, activeTab.title || activeTab.url, "paste");
    setClipStatus(describeIngestResult(result), "success");
  } catch (e) {
    setClipStatus(errorText(e), "error");
  } finally {
    clipSelectionBtn.disabled = false;
  }
});

loadActiveTab();

// --- Preview --------------------------------------------------------------
// Nothing reaches the backend until the user has seen the extraction here.

const previewTitle = document.getElementById("preview-title");
const previewMeta = document.getElementById("preview-meta");
const previewBody = document.getElementById("preview-body");
const previewEdit = document.getElementById("preview-edit");
const previewBack = document.getElementById("preview-back");
const previewAdd = document.getElementById("preview-add");
const previewStatus = document.getElementById("preview-status");

function setPreviewStatus(text, kind) {
  previewStatus.textContent = text;
  previewStatus.className = "status" + (kind ? ` ${kind}` : "");
}

function setPreviewEditable(editable) {
  previewBody.readOnly = !editable;
  previewTitle.readOnly = !editable;
  previewEdit.textContent = editable ? "Done editing" : "Edit";
  previewEdit.setAttribute("aria-pressed", String(editable));
  if (editable) previewBody.focus();
}

function openPreview(extracted) {
  previewTitle.value = extracted.title;
  previewMeta.textContent =
    [extracted.site, extracted.author, extracted.published].filter(Boolean).join(" · ") || extracted.url;
  previewMeta.title = `${extracted.url} (${extracted.extractor})`;
  previewBody.value = extracted.markdown;
  previewBody.scrollTop = 0;
  setPreviewEditable(false);
  setPreviewStatus("");
  previewAdd.disabled = false;
  showPanel("preview");
}

previewEdit.addEventListener("click", () => setPreviewEditable(previewBody.readOnly));
previewBack.addEventListener("click", () => showPanel("clip"));

previewAdd.addEventListener("click", async () => {
  const text = previewBody.value.trim();
  if (!text) {
    setPreviewStatus("Nothing to add — the content is empty.", "error");
    return;
  }
  previewAdd.disabled = true;
  setPreviewStatus("Adding to knowledge base…");
  try {
    const result = await ingestText(text, previewTitle.value.trim(), "article_clipper");
    setPreviewStatus(describeIngestResult(result), "success");
  } catch (e) {
    setPreviewStatus(errorText(e), "error");
    previewAdd.disabled = false;
  }
});

// --- Chat -------------------------------------------------------------------

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");

const history = [];
let sending = false;

function scrollToBottom() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function clearHint() {
  const hint = chatLog.querySelector(".hint");
  if (hint) hint.remove();
}

function addMessage(role) {
  clearHint();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  chatLog.appendChild(el);
  scrollToBottom();
  return el;
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message || sending) return;
  sending = true;
  chatInput.value = "";
  chatInput.disabled = true;

  const userEl = addMessage("user");
  userEl.textContent = message;

  const assistantEl = addMessage("assistant");
  assistantEl.innerHTML = '<span class="hint">Thinking…</span>';

  let text = "";
  let sources = [];

  await streamChat(message, history, {
    onSources: (s) => {
      sources = s || [];
    },
    onToken: (t) => {
      text += t;
      assistantEl.innerHTML = renderMarkdownLite(text);
      scrollToBottom();
    },
    onError: (msg) => {
      assistantEl.innerHTML = `<span style="color: var(--danger)">${msg}</span>`;
    },
    onDone: () => {
      if (sources.length) {
        const sourcesEl = document.createElement("div");
        sourcesEl.className = "msg-sources";
        for (const s of sources) {
          const badge = document.createElement("span");
          badge.className = "source-badge";
          badge.textContent = s.file_path;
          sourcesEl.appendChild(badge);
        }
        assistantEl.appendChild(sourcesEl);
      }
      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: text });
      scrollToBottom();
    },
  });

  sending = false;
  chatInput.disabled = false;
  chatInput.focus();
});
