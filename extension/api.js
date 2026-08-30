import { getAccessToken } from "./auth.js";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

/**
 * Every backend call goes through here so the bearer token is attached in one
 * place. The extension holds the same short-lived token the dashboard does and
 * gets exactly the same answers -- the backend derives the user from it and
 * would ignore any id the extension tried to name.
 */
async function apiFetch(input, init = {}) {
  const token = await getAccessToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    throw new Error("Your session has expired. Please sign in again.");
  }
  return res;
}

export async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.local.get("backendUrl");
  return backendUrl || DEFAULT_BACKEND_URL;
}

export async function setBackendUrl(url) {
  await chrome.storage.local.set({ backendUrl: url });
}

async function parseErrorDetail(res) {
  try {
    const body = await res.json();
    return body.detail ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function ingestUrl(url) {
  const base = await getBackendUrl();
  const res = await apiFetch(`${base}/api/v1/ingest/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function ingestText(text, title, sourceType) {
  const base = await getBackendUrl();
  const res = await apiFetch(`${base}/api/v1/ingest/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, title, source_type: sourceType }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

// PDFs go to the backend's PyMuPDF parser rather than the DOM extractor:
// fetch the bytes (activeTab grants access to the tab's own origin) and post
// them to the same endpoint the dashboard's file upload uses.
export async function ingestPdfUrl(url) {
  let blob;
  try {
    // Deliberately a bare fetch: this hits the article's own origin, and the
    // access token must never be sent anywhere but our backend.
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    blob = await res.blob();
  } catch (e) {
    throw new Error(`Could not download the PDF (${e.message}).`);
  }

  const name = decodeURIComponent(new URL(url).pathname.split("/").pop() || "document.pdf");
  const form = new FormData();
  form.append("files", blob, name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`);

  const base = await getBackendUrl();
  const res = await apiFetch(`${base}/api/v1/ingest/file`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseErrorDetail(res));

  const { results, errors } = await res.json();
  if (!results?.length) throw new Error(errors?.[0]?.error ?? "The backend could not read that PDF.");
  return results[0];
}

export async function streamChat(message, history, handlers, signal) {
  const base = await getBackendUrl();
  let res;
  try {
    res = await apiFetch(`${base}/api/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history }),
      signal,
    });
  } catch (e) {
    handlers.onError?.(e instanceof Error ? e.message : String(e));
    return;
  }

  if (!res.ok || !res.body) {
    handlers.onError?.(await parseErrorDetail(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5).trim());

      if (event.type === "sources") handlers.onSources?.(event.sources);
      else if (event.type === "token") handlers.onToken?.(event.text);
      else if (event.type === "error") handlers.onError?.(event.message);
      else if (event.type === "done") handlers.onDone?.();
    }
  }
}
