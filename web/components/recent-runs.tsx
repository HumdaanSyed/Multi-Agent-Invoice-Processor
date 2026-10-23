"use client";

import { ChevronRight, Inbox } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Card, EmptyState, ErrorBox, tableHead } from "@/components/primitives";
import { RunStatusChip } from "@/components/run-status";
import type { RunStatus, RunSummary } from "@/lib/types";

type Filter = "all" | "needs_review" | "completed" | "other";

const FILTERS: { id: Filter; label: string; match: (s: RunStatus) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "needs_review", label: "Needs review", match: (s) => s === "needs_review" },
  { id: "completed", label: "Completed", match: (s) => s === "completed" },
  { id: "other", label: "Other", match: (s) => s === "processing" || s === "failed" || s === "skipped" },
];

/** Every doc_type except "skipped" is an invoice by construction - the router
 * sends anything else straight to END (app/service.py's derive_status). */
function docTypeLabel(run: RunSummary): string {
  return run.status === "skipped" ? (run.doc_type ?? "Not an invoice") : "Invoice";
}

export function RecentRuns({ runs }: { runs: (RunSummary & { started: string })[] | null }) {
  const [filter, setFilter] = useState<Filter>("all");

  if (runs === null) {
    return <ErrorBox>Couldn&apos;t load recent runs from the backend.</ErrorBox>;
  }

  if (runs.length === 0) {
    return (
      <Card className="grid min-h-96 place-items-center">
        <EmptyState icon={Inbox} title="No invoices yet">
          Upload a PDF invoice and its run will appear here, with its status and a link to every detail.
        </EmptyState>
      </Card>
    );
  }

  const active = FILTERS.find((f) => f.id === filter)!;
  const shown = runs.filter((r) => active.match(r.status));

  return (
    <Card className="min-w-0" data-testid="recent-runs">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Recent runs</h2>
        <div role="tablist" className="flex gap-1 overflow-x-auto [scrollbar-width:none] rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
          {FILTERS.map(({ id, label, match }) => (
            <button
              key={id}
              role="tab"
              aria-selected={filter === id}
              onClick={() => setFilter(id)}
              data-testid={`filter-${id}`}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                filter === id ? "bg-white shadow-sm dark:bg-slate-950" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
              }`}
            >
              {label} <span className="tabular-nums text-slate-400">{runs.filter((r) => match(r.status)).length}</span>
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-500">No runs with this status.</p>
      ) : (
        <table className="w-full table-fixed text-sm">
          <thead className={tableHead}>
            <tr>
              <th className="w-32 pb-2 pr-3 font-medium">Status</th>
              <th className="hidden w-28 pb-2 pr-3 font-medium sm:table-cell">Document</th>
              <th className="hidden pb-2 pr-3 font-medium md:table-cell">Thread</th>
              <th className="pb-2 pr-3 text-right font-medium">Started</th>
              <th className="w-6 pb-2" aria-hidden />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {shown.map((run, i) => (
              <tr
                key={run.thread_id}
                className="group relative animate-[fade-in_0.3s_ease-out_both] hover:bg-slate-50 dark:hover:bg-slate-800/50"
                style={{ animationDelay: `${i * 40}ms` }}
                data-testid="run-row"
              >
                <td className="py-2.5 pr-3">
                  <Link href={`/runs/${run.thread_id}`} className="after:absolute after:inset-0" aria-label={`Open run ${run.thread_id}`}>
                    <RunStatusChip status={run.status} />
                  </Link>
                </td>
                <td className="hidden py-2.5 pr-3 capitalize text-slate-600 sm:table-cell dark:text-slate-300">{docTypeLabel(run)}</td>
                <td className="hidden truncate py-2.5 pr-3 font-mono text-xs text-slate-500 md:table-cell">{run.thread_id}</td>
                <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{run.started}</td>
                <td className="py-2.5 text-slate-300 group-hover:text-teal-500 dark:text-slate-600">
                  <ChevronRight className="size-4" aria-hidden />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
