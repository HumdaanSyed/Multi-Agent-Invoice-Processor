"use client";

import { FileText, ScanText } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Card, EmptyState } from "@/components/primitives";

const kb = (n: number) => `${Math.max(1, Math.round(n / 1024)).toLocaleString()} KB`;

/**
 * The source PDF: the File this tab uploaded (lib/run-file-cache.ts) during
 * a live run, otherwise the signed Supabase link a completed run's detail
 * fetch returns (app/routes.py's get_invoice_run). While Claude is reading
 * it, a soft band drifts down the page.
 */
export function PdfPreview({ file, pdfUrl, reading = false }: { file: File | undefined; pdfUrl?: string | null; reading?: boolean }) {
  const objectUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);

  // Revoking is the side effect that needs an effect; computing the URL isn't.
  useEffect(() => {
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [objectUrl]);

  const url = objectUrl ?? pdfUrl ?? null;

  return (
    <Card className="p-3" data-testid="pdf-preview">
      <div className="relative overflow-hidden rounded-xl bg-slate-100 dark:bg-slate-800">
        {url ? (
          <iframe src={url} title="Source invoice PDF" className="block h-[520px] w-full" />
        ) : (
          <div className="grid h-72 place-items-center p-6">
            <EmptyState icon={FileText} title="No preview">
              This tab didn&apos;t upload the run, and its stored PDF isn&apos;t available yet.
            </EmptyState>
          </div>
        )}
        {reading && (
          <div className="pointer-events-none absolute inset-0" data-testid="reading-overlay">
            <div className="absolute inset-0 bg-teal-500/5" />
            <div className="absolute inset-x-0 h-24 animate-[read-band_2.2s_ease-in-out_infinite] bg-gradient-to-b from-transparent via-teal-400/25 to-transparent" />
            <span className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-white/90 px-3 py-1 text-xs font-medium text-teal-700 shadow-sm dark:bg-slate-900/90 dark:text-teal-300">
              <ScanText className="size-3.5" aria-hidden /> Claude is reading this invoice
            </span>
          </div>
        )}
      </div>
      <div className="mt-2 flex justify-between gap-3 px-1 text-xs text-slate-500">
        <span className="truncate">{file?.name ?? (pdfUrl ? "Stored copy (Supabase)" : "Source PDF")}</span>
        {file && <span className="shrink-0 tabular-nums">{kb(file.size)}</span>}
      </div>
    </Card>
  );
}
