---
name: react-chat-ui
description: Build a React + Vite + Tailwind chat interface that consumes an SSE stream, renders tokens as they arrive, and shows a sources/citations panel. Use when the user asks to build or fix the chat UI, debug streaming text that renders choppy or out of order, or add a sources panel to the interface.
---

# React chat UI for streaming RAG responses

## Consuming the SSE stream

`EventSource` doesn't support custom headers or POST bodies, so for a query-param GET endpoint it works directly; for anything needing auth headers, use `fetch` with a `ReadableStream` reader instead:

```jsx
async function streamChat(query, onSources, onToken, onDone) {
  const res = await fetch(`/chat/stream?q=${encodeURIComponent(query)}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n\n");
    buffer = lines.pop(); // keep incomplete chunk for next read

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "sources") onSources(event.sources);
      else if (event.type === "token") onToken(event.text);
      else if (event.type === "done") onDone();
    }
  }
}
```

## Rendering tokens without janky re-renders

Append tokens to a ref-backed string and flush to state on a small interval (or via `requestAnimationFrame`) rather than calling `setState` on every single token — otherwise a long answer causes a re-render per token and the UI visibly stutters:

```jsx
const [displayText, setDisplayText] = useState("");
const bufferRef = useRef("");

function onToken(text) {
  bufferRef.current += text;
  setDisplayText(bufferRef.current); // fine at typical token rates; batch via rAF if you see jank
}
```

## Sources panel pattern

Render sources as soon as the `sources` event arrives (before any answer tokens), collapsed by default with a count badge, expandable to show the chunk text and source URL. This gives the user something to look at during the retrieval delay and doubles as your own debugging tool for judging retrieval quality.

## Component structure

```
ChatWindow
├── MessageList
│   └── Message (role: user | assistant, streaming: bool)
├── SourcesPanel (collapsible, shows during + after streaming)
└── ChatInput
```

Keep `Message` dumb (just renders text + role); keep streaming state and the SSE connection in `ChatWindow` so re-mounting a message component mid-stream can't drop tokens.

## Common pitfalls

- Not handling partial SSE frames — a `TextDecoder` chunk can split a `data: {...}` line mid-JSON. Always buffer and split on `\n\n`, never `JSON.parse` a raw chunk directly (see the buffer pattern above).
- Auto-scroll fighting the user — only auto-scroll to bottom if the user was already at the bottom before the new token arrived; otherwise scrolling mid-read is disorienting.
- Forgetting to abort the fetch/reader when the user navigates away or starts a new query — leaks an open connection and can cause two streams to write to the same message.
