"use client";

import { FileUp, Loader2, UploadCloud } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ErrorBox } from "@/components/primitives";
import { ApiError, reserveRun } from "@/lib/api";
import { setRunFile } from "@/lib/run-file-cache";

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

export function UploadZone() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    // Guards every entry point at once (click, drag-drop, keyboard, the
    // hidden input) so a second reserveRun() can't fire while one is in flight.
    if (isSubmitting) return;
    if (!isPdf(file)) {
      setError(`"${file.name}" isn't a PDF. Verity only reads PDF invoices.`);
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      // Reserve a thread_id and open its event channel *before* the upload
      // (docs/api.md's "Streaming"); /runs/[threadId] does the actual upload
      // once it's mounted and subscribed.
      const ticket = await reserveRun();
      setRunFile(ticket.thread_id, file);
      router.push(`/runs/${ticket.thread_id}`);
    } catch (err) {
      setIsSubmitting(false);
      setError(err instanceof ApiError ? err.message : "Couldn't reach the backend. Try again.");
    }
  }

  return (
    <div className="space-y-3">
      <button
        type="button"
        disabled={isSubmitting}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        data-testid="upload-zone"
        className={`group flex w-full flex-col items-center gap-4 rounded-2xl border-2 border-dashed bg-white/70 px-5 py-5 text-center transition disabled:cursor-not-allowed disabled:opacity-60 sm:flex-row sm:text-left dark:bg-slate-900/60 ${
          isDragOver
            ? "border-teal-500 bg-teal-50 dark:bg-teal-950/40"
            : "border-teal-200 hover:border-teal-400 dark:border-teal-900 dark:hover:border-teal-700"
        }`}
      >
        <span className="grid size-12 shrink-0 place-items-center rounded-full bg-teal-50 text-teal-600 transition group-hover:-translate-y-0.5 dark:bg-teal-950 dark:text-teal-400">
          {isSubmitting ? <Loader2 className="size-6 animate-spin" aria-hidden /> : <FileUp className="size-6" aria-hidden />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{isSubmitting ? "Starting…" : "Drop a PDF invoice here"}</span>
          <span className="block text-sm text-slate-500">PDF only · up to 20 MB · fields appear as Claude reads them</span>
        </span>
        <span className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-teal-600/25 transition group-hover:bg-teal-500">
          <UploadCloud className="size-4" aria-hidden /> Choose PDF
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        data-testid="file-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void handleFile(file);
        }}
      />
      {error && <ErrorBox>{error}</ErrorBox>}
    </div>
  );
}
