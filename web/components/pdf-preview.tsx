"use client";

import { useEffect, useMemo } from "react";

/**
 * Renders the source PDF, preferring the client-side File object this tab
 * itself uploaded (see lib/run-file-cache.ts) - available only for the
 * duration of a live run in the tab that started it. `pdfUrl` (a signed
 * Supabase Storage link, only ever set on a completed run's detail fetch -
 * see app/routes.py's get_invoice_run) is the fallback for every other
 * case: a revisit, a reload, a link shared with someone else. Neither
 * being available (an in-progress run visited from elsewhere, or a
 * completed run whose invoice has no pdf_storage_path for some reason)
 * shows a placeholder instead of a broken preview.
 */
export function PdfPreview({ file, pdfUrl }: { file: File | undefined; pdfUrl?: string | null }) {
  const objectUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);

  // Revoking is the side effect that needs an effect - computing the URL
  // itself doesn't, so it stays in useMemo rather than useState+setState.
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const url = objectUrl ?? pdfUrl ?? null;

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
