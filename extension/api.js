const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

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
  const res = await fetch(`${base}/api/v1/ingest/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function ingestText(text, title) {
  const base = await getBackendUrl();
  const res = await fetch(`${base}/api/v1/ingest/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, title }),
  });
  if (!res.ok) throw new Error(await parseErrorDetail(res));
  return res.json();
}

export async function streamChat(message, history, handlers, signal) {
  const base = await getBackendUrl();
  let res;
  try {
    res = await fetch(`${base}/api/v1/chat/stream`, {
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
