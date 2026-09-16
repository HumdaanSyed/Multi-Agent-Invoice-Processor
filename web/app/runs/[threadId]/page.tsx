"use client";

import { use } from "react";
import { ExtractionPanel } from "@/components/extraction-panel";
import { PdfPreview } from "@/components/pdf-preview";
import { ReviewForm } from "@/components/review-form";
import { StageIndicator } from "@/components/stage-indicator";
import { ValidationPanel } from "@/components/validation-panel";
import { useRun } from "@/hooks/use-run";
import { getRunFile } from "@/lib/run-file-cache";

export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  const { threadId } = use(params);
  const file = getRunFile(threadId);
  const { run, stage, fields, receivedFields, checks, checksSettling, ledgerStatus, resuming, resume, error } =
    useRun(threadId);

  const status = run?.status;
  // The review form takes over, exclusively, only once needs_review has
  // fully settled (the initial staggered reveal is done) and no resume is
  // in flight. Everything else - including a resume, which re-enters
  // validation and re-runs the exact same reveal (docs/FRONTEND_PLAN.md's
  // Phase 9C instruction 6) - shows the live extraction/validation grid,
  // matching how needs_review's own initial reveal already worked in
  // Phase 9B before this form existed.
  const showReviewForm = status === "needs_review" && !checksSettling && !resuming;
  const showLiveView = status !== "skipped" && status !== "failed" && !showReviewForm;
  const failedChecks = checks.filter((check) => !check.skipped && !check.passed);

  return (
    <div className="flex flex-col gap-6">
      <StageIndicator stage={stage} />

      {error && <p className="text-sm text-error">{error}</p>}

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

      {status === "completed" && !checksSettling && (
        <div className="rounded-lg border border-ok/30 bg-surface p-6">
          <p className="font-medium text-ok">
            {ledgerStatus === "written"
              ? "Added to ledger"
              : ledgerStatus === "failed"
                ? "Processed - the ledger write failed (see server logs)"
                : "Processed"}
          </p>
        </div>
      )}
    </div>
  );
}
