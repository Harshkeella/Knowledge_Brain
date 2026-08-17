"use client";

import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ingestFiles } from "@/lib/api";
import { useKnowledgeStore } from "@/store/knowledge-store";

const ACCEPTED_EXTENSIONS = [".pdf", ".md", ".markdown", ".txt"];

export function Dropzone({ onIngested }: { onIngested: () => void }) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const addUpload = useKnowledgeStore((s) => s.addUpload);
  const updateUpload = useKnowledgeStore((s) => s.updateUpload);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);

    const ids = files.map(() => crypto.randomUUID());
    files.forEach((file, i) => {
      addUpload({ id: ids[i], label: file.name, status: "processing" });
    });

    try {
      const { results, errors } = await ingestFiles(files);

      for (const result of results) {
        const idx = files.findIndex((f) => f.name === result.file_name);
        if (idx !== -1) updateUpload(ids[idx], { status: "done" });
      }
      for (const err of errors) {
        const idx = files.findIndex((f) => f.name === err.file_name);
        if (idx !== -1) updateUpload(ids[idx], { status: "error", error: err.error });
      }
      if (results.length > 0) onIngested();
    } catch (e) {
      ids.forEach((id) =>
        updateUpload(id, {
          status: "error",
          error: e instanceof Error ? e.message : "Upload failed",
        })
      );
    }
  }

  return (
    <Card
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setIsDragging(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      className={`cursor-pointer border-dashed transition-colors ${
        isDragging ? "border-primary bg-accent" : ""
      }`}
    >
      <CardHeader>
        <CardTitle className="text-base">Upload files or folders</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
        <UploadCloud className="size-8" />
        <p className="text-sm">
          Drag & drop PDFs, Markdown, or text files here, or click to browse.
        </p>
        <p className="text-xs">Supported: {ACCEPTED_EXTENSIONS.join(", ")}</p>
        <button
          type="button"
          className="text-xs underline underline-offset-2"
          onClick={(e) => {
            e.stopPropagation();
            folderInputRef.current?.click();
          }}
        >
          or select a folder
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="hidden"
          // @ts-expect-error non-standard attribute for folder selection
          webkitdirectory=""
          onChange={(e) => handleFiles(e.target.files)}
        />
      </CardContent>
    </Card>
  );
}
