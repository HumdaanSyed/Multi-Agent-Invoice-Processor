"use client";

import { Database, Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Chip, secondaryButton } from "@/components/primitives";
import { useLedgerStatusContext } from "@/lib/ledger-status-context";
import { exportCsvUrl, getExportStatus } from "@/lib/api";
import type { ExportStatusResponse } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const POLL_INTERVAL_MS = 20000;

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
  // Shared between both effects below so whichever fetch was issued most
  // recently is the only one allowed to apply its response - without this,
  // the periodic poll and the "written" refetch (independent effects, each
  // firing its own getExportStatus()) can resolve out of order and let an
  // older response overwrite a fresher one, transiently regressing the row
  // count/last-written-at until the next poll tick corrects it.
  const latestRequestIdRef = useRef(0);

  useEffect(() => {
    const load = () => {
      const requestId = ++latestRequestIdRef.current;
      getExportStatus()
        .then((response) => {
          if (requestId !== latestRequestIdRef.current) return;
          setStatus(response);
          setLoadFailed(false);
        })
        .catch(() => {
          if (requestId === latestRequestIdRef.current) setLoadFailed(true);
        });
    };
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  // A ledger row was just (or is about to be) added for the active run -
  // refetch right away instead of waiting up to POLL_INTERVAL_MS for the
  // row count to catch up.
  useEffect(() => {
    if (runLedgerStatus !== "written") return;
    const requestId = ++latestRequestIdRef.current;
    getExportStatus()
      .then((response) => {
        if (requestId === latestRequestIdRef.current) setStatus(response);
      })
      .catch(() => {});
  }, [runLedgerStatus]);

  return (
    <footer className="border-t border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-500" data-testid="ledger-summary">
          <Database className="size-4 shrink-0 text-teal-500" aria-hidden />
          {loadFailed ? (
            <span>Export ledger status is unavailable right now.</span>
          ) : status === null ? (
            <span>Loading export ledger…</span>
          ) : status.row_count === 0 ? (
            <span>One shared ledger of every processed invoice - nothing written yet.</span>
          ) : (
            <span>
              One shared ledger of every processed invoice:{" "}
              <span className="font-semibold tabular-nums text-slate-900 dark:text-slate-100">{status.row_count.toLocaleString()}</span>
              {status.row_count === 1 ? " row" : " rows"}
              {status.last_written_at && <>, last written {formatDateTime(status.last_written_at)}</>}
            </span>
          )}
          {runLedgerStatus === "pending" && (
            <Chip tone="active" data-testid="ledger-status">
              This run: writing…
            </Chip>
          )}
          {runLedgerStatus === "written" && (
            <Chip tone="success" data-testid="ledger-status">
              Added to ledger
            </Chip>
          )}
          {runLedgerStatus === "failed" && (
            <Chip tone="error" data-testid="ledger-status">
              This run&apos;s ledger write failed
            </Chip>
          )}
        </div>
        <a href={exportCsvUrl()} className={secondaryButton} data-testid="export-csv">
          <Download className="size-4" aria-hidden />
          Export CSV
        </a>
      </div>
    </footer>
  );
}
