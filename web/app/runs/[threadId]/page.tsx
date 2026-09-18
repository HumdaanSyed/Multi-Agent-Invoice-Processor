"use client";

import { use, useEffect } from "react";
import { ExtractionPanel } from "@/components/extraction-panel";
import { InvoiceDetail } from "@/components/invoice-detail";
import { PdfPreview } from "@/components/pdf-preview";
import { ReviewForm } from "@/components/review-form";
import { StageIndicator } from "@/components/stage-indicator";
import { ValidationPanel } from "@/components/validation-panel";
import { useRun } from "@/hooks/use-run";
import { useLedgerStatusContext } from "@/lib/ledger-status-context";
import { getRunFile } from "@/lib/run-file-cache";

export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  const { threadId } = use(params);
  const file = getRunFile(threadId);
  const { run, stage, fields, receivedFields, checks, checksSettling, ledgerStatus, resuming, resume, error } =
    useRun(threadId);
  const { setRunLedgerStatus } = useLedgerStatusContext();

  const status = run?.status;

  // Mirrors this run's ledger status into the persistent export bar
  // (components/export-bar.tsx), which renders above this page in the
  // layout and has no other way to see state that lives inside useRun().
  // Prefers the live, SSE-derived ledgerStatus (it resolves first for a run
  // watched live, and doesn't need run to have been refetched yet), falling
  // back to run.ledger_status - the durable field RunResponse now always
  // carries for a completed run, from any endpoint (app/models.py) - for a
  // cold revisit, where no SSE channel ever opens and ledgerStatus would
  // otherwise stay "unknown" forever. "pending" only while genuinely still
  // active (processing, or a resume in flight for a needs_review run) - a
  // skipped/failed run never reaches output() and has nothing to report.
  // Cleanup clears it on navigating away, so the bar doesn't keep showing a
  // stale "this run" status once this page is no longer active.
  useEffect(() => {
    const knownLedgerStatus =
      ledgerStatus === "written" || ledgerStatus === "failed" ? ledgerStatus : (run?.ledger_status ?? null);
    if (knownLedgerStatus === "written" || knownLedgerStatus === "failed") {
      setRunLedgerStatus(knownLedgerStatus);
    } else if (status === "skipped" || status === "failed" || status === "completed") {
      setRunLedgerStatus(null);
    } else if (status !== undefined) {
      setRunLedgerStatus("pending");
    }
    return () => setRunLedgerStatus(null);
  }, [status, ledgerStatus, run?.ledger_status, setRunLedgerStatus]);

  // Deliberately NOT gated on `!resuming`: a resume attempt that never
  // actually re-validates (the corrected invoice fails Pydantic validation
  // server-side before the graph even runs, so no SSE events ever arrive)
  // must not unmount ReviewForm - checksSettling only flips true once a
  // real validation_start/check stream starts, so staying mounted through
  // a failed, event-less resume keeps the user's typed corrections instead
  // of remounting a fresh form from the still-uncorrected invoice. A
  // resume that DOES reach the graph republishes validation_start, which
  // flips checksSettling true and swaps to the live grid below (re-running
  // the exact same reveal - docs/FRONTEND_PLAN.md's Phase 9C instruction
  // 6) - once it settles again, a genuinely new ReviewForm mounts from the
  // freshly re-validated invoice, which is what should happen then.
  const showReviewForm = status === "needs_review" && !checksSettling;
  // The read-only Detail screen (docs/FRONTEND_PLAN.md's Phase 9D, screen
  // 4) - once a completed run is truly at rest (the stagger reveal has
  // finished, not still mid-animation), this replaces the live grid below
  // for both a run just watched live and a cold revisit of an old one.
  const showDetail = status === "completed" && !checksSettling;
  const showLiveView = status !== "skipped" && status !== "failed" && !showReviewForm && !showDetail;
  const failedChecks = checks.filter((check) => !check.skipped && !check.passed);

  return (
    <div className="flex flex-col gap-6">
      <StageIndicator stage={stage} />

      {/* Framed as "your changes weren't saved" rather than a bare error
          dump - the message itself can still be a raw backend validation
          string (e.g. a Pydantic error) until that's addressed server-side,
          but ReviewForm now stays mounted through this (see showReviewForm
          above), so the corrections that triggered it aren't lost either
          way. */}
      {error && <p className="text-sm text-error">Couldn&apos;t save your changes: {error}</p>}

      {status === "skipped" && (
        <div className="rounded-lg border border-border bg-surface p-6">
          <p className="text-text">
            Classified as {run?.doc_type ?? "not an invoice"} - nothing further to process.
          </p>
        </div>
      )}

      {status === "failed" && (
        <div className="rounded-lg border border-error/30 bg-surface p-6">
          <p className="text-error">
            Processing failed{run?.failed_at_node ? ` at "${run.failed_at_node}"` : ""}.
          </p>
        </div>
      )}

      {showLiveView && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <PdfPreview file={file} />
          <div className="flex flex-col gap-6">
            <ExtractionPanel fields={fields} receivedFields={receivedFields} invoice={run?.invoice} />
            <ValidationPanel checks={checks} settling={checksSettling} />
          </div>
        </div>
      )}

      {showReviewForm && run?.invoice && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <PdfPreview file={file} />
          <ReviewForm invoice={run.invoice} failedChecks={failedChecks} onSubmit={resume} submitting={resuming} />
        </div>
      )}

      {showDetail && run?.invoice && (
        <InvoiceDetail
          threadId={threadId}
          invoice={run.invoice}
          validation={run.validation}
          file={file}
          pdfUrl={run.pdf_url}
          traceUrl={run.trace_url}
        />
      )}
    </div>
  );
}
