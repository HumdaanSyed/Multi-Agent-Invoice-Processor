import { CheckCircle2, Clock, Database, TriangleAlert } from "lucide-react";
import { RecentRuns } from "@/components/recent-runs";
import { STEPS } from "@/components/pipeline-steps";
import { Chip, ErrorBox } from "@/components/primitives";
import { UploadZone } from "@/components/upload-zone";
import { getExportStatus, getHealth, listRuns } from "@/lib/api";
import { SERVER_API_BASE_URL } from "@/lib/config";
import type { ExportStatusResponse, RunSummary } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const RECENT_LIMIT = 20;

// Each of these swallows its own failure so one unreachable endpoint doesn't
// blank the whole page; `null` means "couldn't load", distinct from "empty".
async function checkBackend(): Promise<boolean> {
  try {
    return (await getHealth()).status === "ok";
  } catch {
    return false;
  }
}

async function loadRecentRuns(): Promise<RunSummary[] | null> {
  try {
    return (await listRuns(RECENT_LIMIT)).runs;
  } catch {
    return null;
  }
}

async function loadLedger(): Promise<ExportStatusResponse | null> {
  try {
    return await getExportStatus();
  } catch {
    return null;
  }
}

export default async function Home() {
  const [backendOk, runs, ledger] = await Promise.all([checkBackend(), loadRecentRuns(), loadLedger()]);
  const count = (status: RunSummary["status"]) => runs?.filter((r) => r.status === status).length ?? 0;

  const stats = [
    { icon: Database, label: "Ledger rows", value: ledger ? ledger.row_count.toLocaleString() : "—" },
    { icon: Clock, label: "Last written", value: ledger?.last_written_at ? formatDateTime(ledger.last_written_at) : "—" },
    { icon: CheckCircle2, label: `Completed · last ${RECENT_LIMIT}`, value: runs ? count("completed") : "—" },
    { icon: TriangleAlert, label: `Need review · last ${RECENT_LIMIT}`, value: runs ? count("needs_review") : "—" },
  ];

  return (
    <div className="space-y-8">
      {/* Full-width upload hero. */}
      <section
        id="upload"
        className="scroll-mt-24 overflow-hidden rounded-3xl border border-teal-100 bg-gradient-to-br from-teal-50 via-white to-cyan-50 p-6 sm:p-8 dark:border-teal-950 dark:from-teal-950/50 dark:via-slate-900 dark:to-cyan-950/40"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-medium uppercase tracking-wider text-teal-700 dark:text-teal-400">New run</span>
          {backendOk ? <Chip tone="success">Backend ready</Chip> : <Chip tone="error">Backend unreachable</Chip>}
        </div>
        <h1 className="mt-2 max-w-2xl text-2xl font-bold tracking-tight sm:text-3xl">
          Drop an invoice. Watch every field get read and every number get checked.
        </h1>
        <div className="mt-6">
          <UploadZone />
        </div>
        {!backendOk && (
          <div className="mt-4">
            <ErrorBox>
              <strong className="font-semibold">Can&apos;t reach the backend at </strong>
              <span className="break-all font-mono text-xs">{SERVER_API_BASE_URL}</span>
            </ErrorBox>
          </div>
        )}
      </section>

      {/* The five steps, as a horizontal timeline. */}
      <section aria-labelledby="how-heading">
        <h2 id="how-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          What happens to your invoice
        </h2>
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {STEPS.map(({ id, label, where, blurb, icon: Icon }, i) => (
            <li
              key={id}
              className="relative animate-[fade-in_0.3s_ease-out_both] rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
              style={{ animationDelay: `${i * 50}ms` }}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-2xl font-bold tabular-nums text-teal-500/70">{String(i + 1).padStart(2, "0")}</span>
                <Icon className="size-5 text-slate-400" aria-hidden />
              </div>
              <div className="mt-2 text-sm font-semibold">{label}</div>
              <div className="mt-0.5 text-xs text-slate-500">{blurb}</div>
              <span className="mt-3 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {where}
              </span>
              {i < STEPS.length - 1 && (
                <span className="absolute -right-2.5 top-1/2 z-10 hidden size-5 -translate-y-1/2 place-items-center rounded-full border border-slate-200 bg-white text-xs text-slate-400 lg:grid dark:border-slate-700 dark:bg-slate-900" aria-hidden>
                  →
                </span>
              )}
            </li>
          ))}
        </ol>
      </section>

      {/* Runs on the left, a narrow stats column on the right. */}
      <div id="runs" className="grid scroll-mt-24 gap-6 lg:grid-cols-[1fr_260px]">
        <div className="min-w-0">
          {/* Dates are formatted here, on the server, so the client table can't
              hydrate with a different time zone than it was rendered in. */}
          <RecentRuns runs={runs?.map((run) => ({ ...run, started: formatDateTime(run.created_at) })) ?? null} />
        </div>
        <aside className="grid grid-cols-2 content-start gap-3 lg:grid-cols-1" data-testid="stats">
          {stats.map(({ icon: Icon, label, value }) => (
            <div key={label} className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <Icon className="size-3.5" aria-hidden />
                {label}
              </div>
              <div className="mt-1 truncate text-lg font-semibold tabular-nums">{value}</div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}
