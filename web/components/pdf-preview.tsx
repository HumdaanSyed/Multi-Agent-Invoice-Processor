"use client";

import { useEffect, useMemo } from "react";

/**
 * Renders the source PDF from the client-side File object this tab
 * uploaded (see lib/run-file-cache.ts) - there's no backend endpoint yet
 * to fetch an in-progress run's PDF by thread_id, so revisiting a run
 * (no cached File) shows a placeholder instead of a broken preview.
 */
export function PdfPreview({ file }: { file: File | undefined }) {
  const url = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);

  // Revoking is the side effect that needs an effect - computing the URL
  // itself doesn't, so it stays in useMemo rather than useState+setState.
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  if (!url) {
    return (
      <div className="flex h-full min-h-[420px] items-center justify-center rounded-lg border border-border bg-surface">
        <p className="text-sm text-text-muted">PDF preview isn&apos;t available for this run.</p>
      </div>
    );
  }

  return (
    <iframe
      src={url}
      title="Source invoice PDF"
      className="h-full min-h-[420px] w-full rounded-lg border border-border bg-surface"
    />
  );
}
