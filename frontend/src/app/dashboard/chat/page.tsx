"use client";

import { ArrowDown, ArrowUp, PanelLeft, Square } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ChatMessageBubble,
  type ChatMessage,
} from "@/components/chat/chat-message";
import { ProvenancePanel } from "@/components/chat/provenance-panel";
import { SessionSidebar } from "@/components/chat/session-sidebar";
import {
  createSession,
  deleteSession as deleteSessionApi,
  listSessionMessages,
  listSessions,
  renameSession as renameSessionApi,
  streamChat,
  type ChatSession,
  type EvidenceSource,
} from "@/lib/api";

const STARTERS = [
  "What are the main themes across everything I've added?",
  "Summarise the most recent document.",
  "Which entities show up in more than one source?",
];

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openEvidence, setOpenEvidence] = useState<EvidenceSource | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch {
      // The sidebar is an affordance, not the feature. A backend that can't
      // list sessions still lets you ask a question.
    }
  }, []);

  // The initial load sets state from the promise callback rather than the
  // effect body, and drops its result if the component unmounted first -- a
  // slow first response must not overwrite a list the user has already changed.
  useEffect(() => {
    let cancelled = false;
    listSessions().then(
      (loaded) => {
        if (!cancelled) setSessions(loaded);
      },
      () => {}
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Only follow the stream while the user is already at the bottom, so
  // scrolling up to read mid-answer isn't yanked back down.
  useEffect(() => {
    if (atBottom) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, atBottom]);

  // Auto-grow the composer. Layout effect so the height is set before paint
  // and the box never visibly jumps a frame behind the text.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 64);
  }

  function patchMessage(id: string, patch: Partial<ChatMessage>) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m))
    );
  }

  async function ensureSession(): Promise<string | null> {
    if (activeId) return activeId;
    try {
      const session = await createSession();
      setSessions((prev) => [session, ...prev]);
      setActiveId(session.id);
      return session.id;
    } catch {
      // Persistence is best-effort: chat still works unsaved.
      return null;
    }
  }

  function newChat() {
    abortRef.current?.abort();
    setMessages([]);
    setActiveId(null);
    setInput("");
    setMobileOpen(false);
    setAtBottom(true);
  }

  async function selectSession(id: string) {
    abortRef.current?.abort();
    setActiveId(id);
    setMobileOpen(false);
    setAtBottom(true);
    try {
      const stored = await listSessionMessages(id);
      setMessages(
        stored.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          // Persisted evidence, rebuilt into the shape the source list wants,
          // so an old conversation's provenance buttons work like a live one's.
          evidence: m.evidence,
          sources: m.evidence.map((e) => ({
            reference_id: e.reference_id,
            file_path: e.file_path,
          })),
        }))
      );
    } catch {
      setMessages([]);
    }
  }

  async function renameSession(id: string, title: string) {
    setSessions((prev) =>
      prev.map((s) => (s.id === id ? { ...s, title } : s))
    );
    try {
      await renameSessionApi(id, title);
    } catch {
      refreshSessions();
    }
  }

  async function deleteSession(id: string) {
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeId) newChat();
    try {
      await deleteSessionApi(id);
    } catch {
      refreshSessions();
    }
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    const sessionId = await ensureSession();

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
    setAtBottom(true);

    let buffer = "";
    try {
      await streamChat(
        trimmed,
        history,
        {
          onSources: (sources) => patchMessage(assistantId, { sources }),
          onEvidence: (evidence) => patchMessage(assistantId, { evidence }),
          onGrounding: (grounding) => patchMessage(assistantId, { grounding }),
          onTable: (table) => patchMessage(assistantId, { table }),
          onToken: (chunk) => {
            buffer += chunk;
            patchMessage(assistantId, { content: buffer });
          },
          onError: (message) =>
            patchMessage(assistantId, { error: message, streaming: false }),
          onDone: () => patchMessage(assistantId, { streaming: false }),
        },
        controller.signal,
        sessionId
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
      // The title is generated server-side on the first exchange, so the
      // sidebar only learns it after the turn lands.
      refreshSessions();
    }
  }

  function stop() {
    abortRef.current?.abort();
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m))
    );
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const empty = messages.length === 0;

  const composer = (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        send(input);
      }}
      className="relative"
    >
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        placeholder="Ask about your knowledge base…"
        aria-label="Message"
        className="w-full resize-none rounded-2xl border bg-background py-3.5 pr-12 pl-4 text-sm shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
      {isStreaming ? (
        <button
          type="button"
          onClick={stop}
          aria-label="Stop generating"
          className="absolute right-2.5 bottom-2.5 rounded-lg bg-foreground p-2 text-background"
        >
          <Square className="size-3.5 fill-current" />
        </button>
      ) : (
        <button
          type="submit"
          disabled={!input.trim()}
          aria-label="Send"
          className="absolute right-2.5 bottom-2.5 rounded-lg bg-foreground p-2 text-background disabled:opacity-30"
        >
          <ArrowUp className="size-3.5" />
        </button>
      )}
    </form>
  );

  return (
    <div className="flex h-svh overflow-hidden">
      <SessionSidebar
        sessions={sessions}
        activeId={activeId}
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onToggleCollapsed={() => setCollapsed((c) => !c)}
        onCloseMobile={() => setMobileOpen(false)}
        onNewChat={newChat}
        onSelect={selectSession}
        onRename={renameSession}
        onDelete={deleteSession}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-3 py-2.5 md:px-6">
          <button
            onClick={() => setMobileOpen(true)}
            aria-label="Open sidebar"
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted md:hidden"
          >
            <PanelLeft className="size-4" />
          </button>
          <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
            <Image src="/logo.png" alt="" width={22} height={22} priority />
            nodeRels
          </Link>
        </header>

        {empty ? (
          // A new chat centres the composer instead of pinning it to a bottom
          // edge below an empty void.
          <div className="flex flex-1 flex-col items-center justify-center px-4">
            <div className="w-full max-w-3xl">
              <h1 className="mb-6 text-center text-2xl font-semibold">
                What do you want to know?
              </h1>
              {composer}
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    onClick={() => send(starter)}
                    className="rounded-full border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <>
            <div
              ref={scrollRef}
              onScroll={handleScroll}
              className="flex-1 overflow-y-auto px-4 py-6"
            >
              <div className="mx-auto flex max-w-3xl flex-col gap-6">
                {messages.map((m) => (
                  <ChatMessageBubble
                    key={m.id}
                    message={m}
                    onOpenEvidence={setOpenEvidence}
                  />
                ))}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="relative px-4 pb-4">
              {!atBottom && (
                <button
                  onClick={() =>
                    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
                  }
                  aria-label="Jump to latest"
                  className="absolute -top-10 left-1/2 -translate-x-1/2 rounded-full border bg-background p-2 shadow-md hover:bg-muted"
                >
                  <ArrowDown className="size-4" />
                </button>
              )}
              <div className="mx-auto max-w-3xl">{composer}</div>
            </div>
          </>
        )}
      </div>

      {openEvidence && (
        <ProvenancePanel
          evidence={openEvidence}
          onClose={() => setOpenEvidence(null)}
        />
      )}
    </div>
  );
}
