"use client";

import {
  Check,
  Database,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { ChatSession } from "@/lib/api";
import { groupByRecency } from "@/lib/session-groups";
import { cn } from "@/lib/utils";

function SessionRow({
  session,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const title = draft.trim();
    if (title && title !== session.title) onRename(title);
    else setDraft(session.title);
    setEditing(false);
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1 rounded-lg bg-muted px-2 py-1.5">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(session.title);
              setEditing(false);
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          aria-label="Session title"
        />
        <button onClick={commit} aria-label="Save title" className="p-0.5">
          <Check className="size-3.5" />
        </button>
        <button
          onClick={() => {
            setDraft(session.title);
            setEditing(false);
          }}
          aria-label="Cancel rename"
          className="p-0.5"
        >
          <X className="size-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm",
        active ? "bg-muted font-medium" : "hover:bg-muted/60"
      )}
    >
      <button
        onClick={onSelect}
        className="min-w-0 flex-1 truncate text-left"
        title={session.title}
      >
        {session.title}
      </button>
      {/* Hover-revealed, but focus-visible too -- actions that only appear on
          hover are unreachable by keyboard otherwise. */}
      <span className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <button
          onClick={() => {
            setDraft(session.title);
            setEditing(true);
          }}
          aria-label={`Rename ${session.title}`}
          className="rounded p-1 text-muted-foreground hover:text-foreground"
        >
          <Pencil className="size-3.5" />
        </button>
        <button
          onClick={onDelete}
          aria-label={`Delete ${session.title}`}
          className="rounded p-1 text-muted-foreground hover:text-destructive"
        >
          <Trash2 className="size-3.5" />
        </button>
      </span>
    </div>
  );
}

export function SessionSidebar({
  sessions,
  activeId,
  collapsed,
  mobileOpen,
  onToggleCollapsed,
  onCloseMobile,
  onNewChat,
  onSelect,
  onRename,
  onDelete,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapsed: () => void;
  onCloseMobile: () => void;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const groups = groupByRecency(sessions);

  // Collapsed is an icon rail, not a disappearance: the two controls that
  // matter (new chat, knowledge base) stay reachable without expanding.
  if (collapsed) {
    return (
      <nav className="hidden w-14 shrink-0 flex-col items-center gap-2 border-r py-3 md:flex">
        <button
          onClick={onToggleCollapsed}
          aria-label="Expand sidebar"
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <PanelLeftOpen className="size-4" />
        </button>
        <button
          onClick={onNewChat}
          aria-label="New chat"
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <MessageSquarePlus className="size-4" />
        </button>
        <Link
          href="/dashboard/knowledge"
          aria-label="Knowledge base"
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <Database className="size-4" />
        </Link>
      </nav>
    );
  }

  return (
    <>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/30 md:hidden"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}
      <nav
        className={cn(
          "flex w-64 shrink-0 flex-col border-r bg-background",
          // Overlay drawer below md, in-flow column at md and up.
          "fixed inset-y-0 left-0 z-40 transition-transform md:static md:z-auto md:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
        aria-label="Chat sessions"
      >
        <div className="flex items-center gap-1 px-2 py-3">
          <button
            onClick={onNewChat}
            className="flex flex-1 items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted"
          >
            <MessageSquarePlus className="size-4" />
            New chat
          </button>
          <button
            onClick={onToggleCollapsed}
            aria-label="Collapse sidebar"
            className="hidden rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground md:block"
          >
            <PanelLeftClose className="size-4" />
          </button>
          <button
            onClick={onCloseMobile}
            aria-label="Close sidebar"
            className="rounded-lg p-2 text-muted-foreground hover:bg-muted md:hidden"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {groups.length === 0 && (
            <p className="px-2 py-4 text-xs text-muted-foreground">
              No conversations yet.
            </p>
          )}
          {groups.map(({ name, items }) => (
            <div key={name} className="mb-3">
              <h3 className="px-2 py-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                {name}
              </h3>
              <div className="space-y-0.5">
                {items.map((session) => (
                  <SessionRow
                    key={session.id}
                    session={session}
                    active={session.id === activeId}
                    onSelect={() => onSelect(session.id)}
                    onRename={(title) => onRename(session.id, title)}
                    onDelete={() => onDelete(session.id)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* The graph/KB is no longer the landing page, so it needs a home here. */}
        <div className="border-t p-2">
          <Link
            href="/dashboard/knowledge"
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <Database className="size-4" />
            Knowledge base
          </Link>
        </div>
      </nav>
    </>
  );
}
