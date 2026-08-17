import { getBackendUrl, setBackendUrl, ingestUrl, ingestText, streamChat } from "./api.js";
import { renderMarkdownLite } from "./markdown-lite.js";

// --- Tabs ---------------------------------------------------------------

const tabButtons = document.querySelectorAll(".tab-btn");
const panels = { clip: document.getElementById("panel-clip"), chat: document.getElementById("panel-chat") };

for (const btn of tabButtons) {
  btn.addEventListener("click", () => {
    for (const b of tabButtons) {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", String(b === btn));
    }
    for (const [name, panel] of Object.entries(panels)) {
      panel.classList.toggle("hidden", name !== btn.dataset.tab);
    }
  });
}

// --- Settings -------------------------------------------------------------

const settingsToggle = document.getElementById("settings-toggle");
const settingsPanel = document.getElementById("settings-panel");
const backendUrlInput = document.getElementById("backend-url");
const settingsSave = document.getElementById("settings-save");
const settingsStatus = document.getElementById("settings-status");

settingsToggle.addEventListener("click", async () => {
  settingsPanel.classList.toggle("hidden");
  if (!settingsPanel.classList.contains("hidden")) {
    backendUrlInput.value = await getBackendUrl();
  }
});

settingsSave.addEventListener("click", async () => {
  const url = backendUrlInput.value.trim().replace(/\/$/, "");
  if (!url) return;
  await setBackendUrl(url);
  settingsStatus.textContent = "Saved.";
  setTimeout(() => (settingsStatus.textContent = ""), 1500);
});

// --- Clip -----------------------------------------------------------------

const pageTitleEl = document.getElementById("page-title");
const pageUrlEl = document.getElementById("page-url");
const clipPageBtn = document.getElementById("clip-page");
const clipSelectionBtn = document.getElementById("clip-selection");
const clipStatus = document.getElementById("clip-status");

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

clipPageBtn.addEventListener("click", async () => {
  if (!activeTab?.url) return;
  clipPageBtn.disabled = true;
  setClipStatus("Adding page…");
  try {
    const result = await ingestUrl(activeTab.url);
    setClipStatus(describeIngestResult(result), "success");
  } catch (e) {
    setClipStatus(e instanceof Error ? e.message : String(e), "error");
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
    const result = await ingestText(text, activeTab.title || activeTab.url);
    setClipStatus(describeIngestResult(result), "success");
  } catch (e) {
    setClipStatus(e instanceof Error ? e.message : String(e), "error");
  } finally {
    clipSelectionBtn.disabled = false;
  }
});

loadActiveTab();

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
