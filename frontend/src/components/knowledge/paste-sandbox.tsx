"use client";

import { useState } from "react";
import { ClipboardPaste } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ingestText } from "@/lib/api";
import { useKnowledgeStore } from "@/store/knowledge-store";

export function PasteSandbox({ onIngested }: { onIngested: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const addUpload = useKnowledgeStore((s) => s.addUpload);
  const updateUpload = useKnowledgeStore((s) => s.updateUpload);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || submitting) return;

    const label = title.trim() || trimmed.slice(0, 40);
    const id = crypto.randomUUID();
    addUpload({ id, label, status: "processing" });
    setSubmitting(true);
    setText("");
    setTitle("");

    try {
      await ingestText(trimmed, title.trim() || undefined);
      updateUpload(id, { status: "done" });
      onIngested();
    } catch (err) {
      updateUpload(id, {
        status: "error",
        error: err instanceof Error ? err.message : "Ingestion failed",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Paste raw text</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-2">
          <Input
            placeholder="Title (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Textarea
            placeholder="Paste any text here to add it straight to your knowledge base..."
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            required
          />
          <Button type="submit" disabled={submitting} className="self-end">
            <ClipboardPaste className="size-4" />
            Ingest text
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
