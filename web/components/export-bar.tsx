"use client";

import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { useLedgerStatusContext } from "@/lib/ledger-status-context";
import { exportCsvUrl, getExportStatus } from "@/lib/api";
import type { ExportStatusResponse } from "@/lib/types";

const POLL_INTERVAL_MS = 20000;

function formatWrittenAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/**
 * A slim footer present on every screen (docs/FRONTEND_PLAN.md's Phase 9D),
 * rendered once in app/layout.tsx. Two independent pieces of state:
 *
 * - The ledger's own aggregate stats (row count, last write), fetched here
 *   and lightly polled - this is a shared, cross-run number, so a plain
 *   client-side fetch/poll is enough; nothing needs it to be instant.
 * - "This active run's" ledger status, read from LedgerStatusContext
 *   (lib/ledger-status-context.tsx) - written by the run page as its own
 *   useRun()-derived ledgerStatus changes, since that state otherwise has
 *   no way to reach a footer rendered above it in the tree.
 *
 * Explicitly NOT a per-invoice download - the copy says so, matching
 * docs/FRONTEND_PLAN.md instruction 4.
 */
export function ExportBar() {
  const { runLedgerStatus } = useLedgerStatusContext();
  const [status, setStatus] = useState<ExportStatusResponse | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      getExportStatus()
        .then((response) => {
          if (cancelled) return;
          setStatus(response);
          setLoadFailed(false);
        })
        .catch(() => {
          if (!cancelled) setLoadFailed(true);
        });
    };
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // A ledger row was just (or is about to be) added for the active run -
  // refetch right away instead of waiting up to POLL_INTERVAL_MS for the
  // row count to catch up.
  useEffect(() => {
    if (runLedgerStatus !== "written") return;
    getExportStatus()
      .then(setStatus)
      .catch(() => {});
  }, [runLedgerStatus]);

  return (
    <footer className="border-t border-border bg-surface">
      <div className="mx-auto flex max-w-[1100px] flex-wrap items-center justify-between gap-3 px-6 py-4">
        <p className="text-sm text-text-muted">
          {loadFailed ? (
            "Export ledger status is unavailable right now."
          ) : status === null ? (
            "Loading export ledger…"
          ) : status.row_count === 0 ? (
            "One shared ledger of every processed invoice - nothing written yet."
          ) : (
            <>
              One shared ledger of every processed invoice:{" "}
              <span className="font-mono text-text">{status.row_count}</span>
              {status.row_count === 1 ? " row" : " rows"}
              {status.last_written_at && <>, last written {formatWrittenAt(status.last_written_at)}</>}.
            </>
          )}
          {runLedgerStatus === "pending" && <span className="ml-2 text-text-muted">· This run: writing…</span>}
          {runLedgerStatus === "written" && <span className="ml-2 font-medium text-ok">· Added to ledger</span>}
          {runLedgerStatus === "failed" && (
            <span className="ml-2 text-error">· This run&apos;s ledger write failed</span>
          )}
        </p>
        <a href={exportCsvUrl()} className={buttonVariants({ variant: "outline", size: "sm" })}>
          <Download aria-hidden />
          Export CSV
        </a>
      </div>
    </footer>
  );
}
