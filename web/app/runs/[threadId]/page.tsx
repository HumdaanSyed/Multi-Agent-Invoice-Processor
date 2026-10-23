"use client";

import { ArrowLeft, ExternalLink, FileText, FileX2, ReceiptText } from "lucide-react";
import Link from "next/link";
import { use, useEffect } from "react";
import { ExtractionPanel } from "@/components/extraction-panel";
import { InvoiceDetail } from "@/components/invoice-detail";
import { PdfPreview } from "@/components/pdf-preview";
import { derivePipeline, PipelineSteps } from "@/components/pipeline-steps";
import { Card, EmptyState, ErrorBox, secondaryButton, SkeletonBars } from "@/components/primitives";
import { ReviewForm } from "@/components/review-form";
import { RunStatusChip } from "@/components/run-status";
import { ValidationPanel } from "@/components/validation-panel";
import { useRun } from "@/hooks/use-run";
import { useLedgerStatusContext } from "@/lib/ledger-status-context";
import { getRunFile } from "@/lib/run-file-cache";

export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  const { threadId } = use(params);
  const file = getRunFile(threadId);
  const { run, timeline, fields, receivedFields, checks, checksSettling, ledgerStatus, resuming, resume, error } =
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

  const completedChecks: typeof checks =
    status === "completed" && !checks.length
      ? (run?.validation?.checks ?? []).map((c) => ({ type: "check", thread_id: threadId, ...c }))
      : checks;
  const pipeline = derivePipeline({ timeline, run, checks: completedChecks, receivedFields: receivedFields.size, ledgerStatus });
  const extracting = pipeline.state.extract.status === "running";
  const invoice = run?.invoice;
  // The upload's own response only lands once the run pauses or finishes;
  // until then, arriving stream events are the proof it's processing.
  const shownStatus = status ?? (timeline.length > 0 || receivedFields.size > 0 ? "processing" : undefined);
  const title = invoice
    ? invoice.vendor_name
    : status === "skipped"
      ? "Not an invoice"
      : status === "failed"
        ? "Run failed"
        : shownStatus
          ? "Processing invoice"
          : "Loading run…";

  return (
    <div className="space-y-6">
      <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-slate-500 transition hover:text-slate-900 dark:hover:text-white">
        <ArrowLeft className="size-4" aria-hidden /> All runs
      </Link>

      <div className="grid gap-6 lg:grid-cols-[400px_1fr]">
        <div className="min-w-0 space-y-6">
          <PdfPreview file={file} pdfUrl={run?.pdf_url} reading={extracting} />
          <Card>
            <PipelineSteps state={pipeline.state} finished={pipeline.finished} />
          </Card>
        </div>

        <div className="min-w-0 space-y-6">
          <Card className="flex flex-wrap items-center justify-between gap-4" data-testid="run-summary">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-teal-100 text-teal-600 dark:bg-teal-950 dark:text-teal-400">
                <ReceiptText className="size-5" aria-hidden />
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="truncate text-lg font-semibold">{title}</h1>
                  <RunStatusChip status={shownStatus} />
                </div>
                <div className="truncate text-xs text-slate-500">
                  {invoice && (
                    <>
                      Invoice <span className="font-mono">{invoice.invoice_number}</span> ·{" "}
                    </>
                  )}
                  Thread <span className="font-mono">{threadId}</span>
                </div>
              </div>
            </div>
            {(run?.pdf_url || run?.trace_url) && (
              <div className="flex flex-wrap gap-2">
                {run?.pdf_url && (
                  <a href={run.pdf_url} target="_blank" rel="noopener noreferrer" className={secondaryButton}>
                    <FileText className="size-4" aria-hidden /> Source PDF
                  </a>
                )}
                {run?.trace_url && (
                  <a href={run.trace_url} target="_blank" rel="noopener noreferrer" className={secondaryButton}>
                    <ExternalLink className="size-4" aria-hidden /> View trace
                  </a>
                )}
              </div>
            )}
          </Card>

          {/* Framed as "your changes weren't saved": ReviewForm stays mounted
              through this (see showReviewForm above), so nothing typed is lost. */}
          {error && (
            <ErrorBox>
              <strong className="font-semibold">Couldn&apos;t save your changes: </strong>
              {error}
            </ErrorBox>
          )}

          {status === "failed" && (
            <ErrorBox>
              <strong className="font-semibold">Processing failed</strong>
              {run?.failed_at_node ? ` at the "${run.failed_at_node}" step.` : "."} The full error is in the server log.
            </ErrorBox>
          )}

          {status === "skipped" && (
            <Card className="grid min-h-96 place-items-center">
              <EmptyState icon={FileX2} title="Nothing to process">
                Claude classified this document as {run?.doc_type ?? "not an invoice"}, so it stopped after the first step.
              </EmptyState>
            </Card>
          )}

          {showLiveView &&
            (run || receivedFields.size > 0 || timeline.length > 0 ? (
              <>
                <ExtractionPanel fields={fields} receivedFields={receivedFields} invoice={run?.invoice} reading={extracting} />
                <ValidationPanel checks={checks} settling={checksSettling} />
              </>
            ) : (
              <Card className="grid min-h-96 place-items-center">
                <SkeletonBars label="Connecting to the run…" />
              </Card>
            ))}

          {showReviewForm && run?.invoice && (
            <ReviewForm invoice={run.invoice} failedChecks={failedChecks} flags={run.flags ?? []} onSubmit={resume} submitting={resuming} />
          )}

          {showDetail && run?.invoice && (
            <InvoiceDetail threadId={threadId} invoice={run.invoice} validation={run.validation} workMs={pipeline.workMs} />
          )}
        </div>
      </div>
    </div>
  );
}
