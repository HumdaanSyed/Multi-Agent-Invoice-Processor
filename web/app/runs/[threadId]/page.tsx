"use client";

import { use } from "react";
import { ExtractionPanel } from "@/components/extraction-panel";
import { PdfPreview } from "@/components/pdf-preview";
import { StageIndicator } from "@/components/stage-indicator";
import { ValidationPanel } from "@/components/validation-panel";
import { useRun } from "@/hooks/use-run";
import { getRunFile } from "@/lib/run-file-cache";

export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  const { threadId } = use(params);
  const file = getRunFile(threadId);
  const { run, stage, fields, receivedFields, checks, checksSettling, ledgerStatus, error } = useRun(threadId);

  const status = run?.status;
  const showLiveView = status !== "skipped" && status !== "failed";

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

      {status === "needs_review" && !checksSettling && (
        <div className="rounded-lg border border-flag/30 bg-surface p-6">
          <p className="font-medium text-flag">Needs review</p>
          <ul className="mt-2 list-disc pl-5 text-sm text-text">
            {(run?.flags ?? []).map((flag) => (
              <li key={flag}>{flag}</li>
            ))}
          </ul>
          {/* Editable fields + the resume action arrive in Phase 9C
              (docs/FRONTEND_PLAN.md) - this is read-only for now. */}
          <p className="mt-3 text-sm text-text-muted">
            Correcting and resubmitting flagged fields isn&apos;t built yet.
          </p>
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
