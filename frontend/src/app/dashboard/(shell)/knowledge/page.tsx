"use client";

import { useCallback, useEffect, useState } from "react";
import { Dropzone } from "@/components/knowledge/dropzone";
import { FolderInput } from "@/components/knowledge/folder-input";
import { InventoryTable } from "@/components/knowledge/inventory-table";
import { PasteSandbox } from "@/components/knowledge/paste-sandbox";
import { UploadStatusList } from "@/components/knowledge/upload-status-list";
import { UrlInput } from "@/components/knowledge/url-input";
import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { deleteKnowledgeDocument, listKnowledgeBase } from "@/lib/api";
import { useKnowledgeStore } from "@/store/knowledge-store";

export default function KnowledgePage() {
  const documents = useKnowledgeStore((s) => s.documents);
  const setDocuments = useKnowledgeStore((s) => s.setDocuments);
  const removeDocument = useKnowledgeStore((s) => s.removeDocument);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setDocuments(await listKnowledgeBase());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }, [setDocuments]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleDelete(docId: string) {
    await deleteKnowledgeDocument(docId);
    removeDocument(docId);
  }

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold">Knowledge Base</h1>
        <p className="text-muted-foreground">
          Upload documents, scan a folder, add URLs, or paste text to build your
          hybrid vector + graph knowledge base.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Dropzone onIngested={refresh} />
        <FolderInput onIngested={refresh} />
        <UrlInput onIngested={refresh} />
        <PasteSandbox onIngested={refresh} />
      </div>

      <UploadStatusList />

      <div>
        <h2 className="mb-3 text-lg font-medium">Inventory</h2>
        {error ? (
          <div className="flex items-center justify-between gap-4 rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <span className="flex items-center gap-2">
              <AlertCircle className="size-4 shrink-0" />
              Couldn&apos;t reach the knowledge base API: {error}
            </span>
            <Button variant="outline" size="sm" onClick={refresh}>
              Retry
            </Button>
          </div>
        ) : (
          <InventoryTable
            documents={documents}
            isLoading={isLoading}
            onDelete={handleDelete}
          />
        )}
      </div>
    </div>
  );
}
