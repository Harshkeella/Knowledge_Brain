"use client";

import { useState } from "react";
import { Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ingestUrl } from "@/lib/api";
import { useKnowledgeStore } from "@/store/knowledge-store";

export function UrlInput({ onIngested }: { onIngested: () => void }) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const addUpload = useKnowledgeStore((s) => s.addUpload);
  const updateUpload = useKnowledgeStore((s) => s.updateUpload);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed || submitting) return;

    const id = crypto.randomUUID();
    addUpload({ id, label: trimmed, status: "processing" });
    setSubmitting(true);
    setUrl("");

    try {
      await ingestUrl(trimmed);
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
        <CardTitle className="text-base">Add from a URL</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex gap-2">
          <Input
            type="url"
            placeholder="Paste an article or YouTube video link..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <Button type="submit" disabled={submitting}>
            <Link2 className="size-4" />
            Add
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
