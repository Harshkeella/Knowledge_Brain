import { create } from "zustand";
import type { KnowledgeDocument } from "@/lib/api";

export type UploadStatus = "pending" | "processing" | "done" | "error";

export interface UploadItem {
  id: string;
  label: string;
  status: UploadStatus;
  error?: string;
}

interface KnowledgeState {
  uploads: UploadItem[];
  documents: KnowledgeDocument[];

  addUpload: (item: UploadItem) => void;
  updateUpload: (id: string, patch: Partial<UploadItem>) => void;
  clearFinishedUploads: () => void;

  setDocuments: (docs: KnowledgeDocument[]) => void;
  removeDocument: (docId: string) => void;
}

export const useKnowledgeStore = create<KnowledgeState>((set) => ({
  uploads: [],
  documents: [],

  addUpload: (item) =>
    set((state) => ({ uploads: [item, ...state.uploads] })),

  updateUpload: (id, patch) =>
    set((state) => ({
      uploads: state.uploads.map((u) => (u.id === id ? { ...u, ...patch } : u)),
    })),

  clearFinishedUploads: () =>
    set((state) => ({
      uploads: state.uploads.filter(
        (u) => u.status !== "done" && u.status !== "error"
      ),
    })),

  setDocuments: (docs) => set({ documents: docs }),

  removeDocument: (docId) =>
    set((state) => ({
      documents: state.documents.filter((d) => d.doc_id !== docId),
    })),
}));
