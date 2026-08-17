"use client";

import { useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { KnowledgeDocument } from "@/lib/api";

function formatSize(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function InventoryTable({
  documents,
  isLoading,
  onDelete,
}: {
  documents: KnowledgeDocument[];
  isLoading: boolean;
  onDelete: (docId: string) => Promise<void>;
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function handleDelete(docId: string) {
    setDeletingId(docId);
    try {
      await onDelete(docId);
    } finally {
      setDeletingId(null);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10 text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        Your knowledge base is empty. Upload a file, add a URL, or paste some
        text to get started.
      </p>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>File Name</TableHead>
          <TableHead>Source Type</TableHead>
          <TableHead className="text-right">Chunk Count</TableHead>
          <TableHead className="text-right">Storage Footprint</TableHead>
          <TableHead>Date Added</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => (
          <TableRow key={doc.doc_id}>
            <TableCell className="max-w-xs truncate font-medium">
              {doc.file_name}
            </TableCell>
            <TableCell>
              <Badge variant="outline">{doc.source_type}</Badge>
            </TableCell>
            <TableCell className="text-right">{doc.chunk_count}</TableCell>
            <TableCell className="text-right">{formatSize(doc.size_bytes)}</TableCell>
            <TableCell>{formatDate(doc.date_added)}</TableCell>
            <TableCell className="text-right">
              <Button
                variant="ghost"
                size="icon"
                disabled={deletingId === doc.doc_id}
                onClick={() => handleDelete(doc.doc_id)}
                aria-label={`Delete ${doc.file_name} from knowledge base`}
              >
                {deletingId === doc.doc_id ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Trash2 className="size-4" />
                )}
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
