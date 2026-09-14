"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ApiError, reserveRun } from "@/lib/api";
import { setRunFile } from "@/lib/run-file-cache";
import { cn } from "@/lib/utils";

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
    if (!isPdf(file)) {
      setError(`"${file.name}" isn't a PDF. Verity only reads PDF invoices.`);
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      // Reserve a thread_id and open its event channel *before* the actual
      // upload (docs/api.md's "Streaming") - by the time POST /invoices
      // returns, every event it would ever publish has already happened,
      // so a client that wants to watch it live has to subscribe first.
      // /runs/[threadId] does the actual upload once it's mounted and
      // subscribed.
      const ticket = await reserveRun();
      setRunFile(ticket.thread_id, file);
      router.push(`/runs/${ticket.thread_id}`);
    } catch (err) {
      setIsSubmitting(false);
      setError(err instanceof ApiError ? err.message : "Couldn't reach the backend. Try again.");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
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
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-16 text-center transition-colors",
          isDragOver ? "border-brand bg-brand-soft" : "border-border bg-surface",
          isSubmitting && "pointer-events-none opacity-60",
        )}
      >
        <p className="text-base text-text">
          {isSubmitting ? "Starting…" : "Drop a PDF invoice here, or click to choose one"}
        </p>
        <p className="text-sm text-text-muted">PDF only</p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) void handleFile(file);
          }}
        />
      </div>
      {error && <p className="text-sm text-error">{error}</p>}
    </div>
  );
}
