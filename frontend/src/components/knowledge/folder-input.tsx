"use client";

import { useState } from "react";
import { FolderCog } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ingestFolder } from "@/lib/api";
import { useKnowledgeStore } from "@/store/knowledge-store";

/**
 * Path-based: the folder is read from the server's own disk, not uploaded.
 * A `webkitdirectory` picker would re-post every byte of a repo through
 * multipart just to rebuild a tree the backend can already see.
 */
export function FolderInput({ onIngested }: { onIngested: () => void }) {
  const [path, setPath] = useState("");
  const [indexDocuments, setIndexDocuments] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const addUpload = useKnowledgeStore((s) => s.addUpload);
  const updateUpload = useKnowledgeStore((s) => s.updateUpload);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = path.trim();
    if (!trimmed || submitting) return;

    const id = crypto.randomUUID();
    addUpload({ id, label: trimmed, status: "processing" });
    setSubmitting(true);
    setPath("");

    try {
      const result = await ingestFolder({ path: trimmed, indexDocuments });
      updateUpload(id, {
        status: "done",
        // The tree is committed by the time this resolves; documents keep
        // indexing in the background, so say so rather than implying done.
        label:
          `${result.name} — ${result.files} files, ${result.functions} functions` +
          (result.documents_pending
            ? `, ${result.documents_pending} document(s) still indexing`
            : ""),
      });
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
        <CardTitle className="text-base">Add a folder</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-col gap-2">
          <div className="flex gap-2">
            <Input
              placeholder="C:\\path\\to\\project"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              required
            />
            <Button type="submit" disabled={submitting}>
              <FolderCog className="size-4" />
              Scan
            </Button>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={indexDocuments}
              onChange={(e) => setIndexDocuments(e.target.checked)}
              className="size-3.5 accent-current"
            />
            Also index PDFs, Markdown, text and spreadsheets found inside
          </label>
        </form>
      </CardContent>
    </Card>
  );
}
