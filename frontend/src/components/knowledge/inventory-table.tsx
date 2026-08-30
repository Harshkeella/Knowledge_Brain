"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2, Share2, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { KnowledgeDocument } from "@/lib/api";
import { symbolFor } from "@/constants/symbols";

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

  async function handleDelete(doc: KnowledgeDocument) {
    // Deleting a folder source cascades every Folder/File/Class/Function node
    // under it -- hundreds of nodes from one click. Native confirm(): a modal
    // component for a yes/no question is a dependency and three files.
    const scope =
      doc.source_type === "folder"
        ? "and every folder, file and code symbol under it"
        : "and everything extracted from it";
    if (!window.confirm(`Delete "${doc.file_name}" ${scope}?`)) return;

    setDeletingId(doc.doc_id);
    try {
      await onDelete(doc.doc_id);
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
              <Badge variant="outline" className="gap-1.5">
                {(() => {
                  // Same registry the graph legend reads, so a row and its
                  // supernode can never disagree about what they are.
                  const Icon = symbolFor("source", doc.source_type)?.icon;
                  return Icon ? <Icon aria-hidden className="size-3.5" /> : null;
                })()}
                {doc.source_type}
              </Badge>
            </TableCell>
            <TableCell className="text-right">{doc.chunk_count}</TableCell>
            <TableCell className="text-right">{formatSize(doc.size_bytes)}</TableCell>
            <TableCell>{formatDate(doc.date_added)}</TableCell>
            <TableCell className="text-right">
              <Link
                href={`/dashboard/graph?focus=${encodeURIComponent(
                  `source:${doc.file_name}`
                )}`}
                aria-label={`Show ${doc.file_name} in the graph`}
                className={buttonVariants({ variant: "ghost", size: "icon" })}
              >
                <Share2 className="size-4" />
              </Link>
              <Button
                variant="ghost"
                size="icon"
                disabled={deletingId === doc.doc_id}
                onClick={() => handleDelete(doc)}
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
