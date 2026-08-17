"use client";

import { Loader2, Send } from "lucide-react";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  ChatMessageBubble,
  type ChatMessage,
} from "@/components/chat/chat-message";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { streamChat } from "@/lib/api";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (atBottomRef.current) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [messages]);

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    atBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  function patchMessage(id: string, patch: Partial<ChatMessage>) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m))
    );
  }

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const history = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const assistantId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: trimmed },
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setIsStreaming(true);

    let buffer = "";
    try {
      await streamChat(
        trimmed,
        history,
        {
          onSources: (sources) => patchMessage(assistantId, { sources }),
          onToken: (text) => {
            buffer += text;
            patchMessage(assistantId, { content: buffer });
          },
          onError: (message) =>
            patchMessage(assistantId, { error: message, streaming: false }),
          onDone: () => patchMessage(assistantId, { streaming: false }),
        },
        controller.signal
      );
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        patchMessage(assistantId, {
          error: err instanceof Error ? err.message : "Chat failed.",
          streaming: false,
        });
      }
    } finally {
      setIsStreaming(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Chat</h1>
        <p className="text-muted-foreground">
          Ask questions grounded in your knowledge base.
        </p>
      </div>

      <div
        onScroll={handleScroll}
        className="flex-1 space-y-4 overflow-y-auto rounded-lg border p-4"
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center gap-4 py-16">
            <Image
              src="/logo.png"
              alt=""
              width={64}
              height={64}
              className="rounded-xl opacity-90"
            />
            <p className="text-center text-sm text-muted-foreground">
              Ask a question about anything you&apos;ve added to the knowledge
              base.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <ChatMessageBubble key={m.id} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
        className="flex gap-2"
      >
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your knowledge base... (Enter to send, Shift+Enter for a new line)"
          className="max-h-40 min-h-12"
        />
        <Button type="submit" disabled={isStreaming || !input.trim()}>
          {isStreaming ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          Send
        </Button>
      </form>
    </div>
  );
}
