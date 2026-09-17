import Link from "next/link";
import { RunStatusDot } from "@/components/run-status";
import { UploadZone } from "@/components/upload-zone";
import { getHealth, listRuns } from "@/lib/api";
import { API_BASE_URL, PRODUCT_TAGLINE } from "@/lib/config";
import type { RunSummary } from "@/lib/types";

// This calls a live backend on every request - without this, Next.js has
// no dynamic API to key off and would prerender the page once at build
// time, freezing "connected"/"unreachable" as a static snapshot forever.
export const dynamic = "force-dynamic";

/**
 * Runs on the Next.js server, not the browser - a plain server-component
 * fetch, no client JS needed for this scaffold-stage check. Phase 9B
 * replaces this whole page with the real upload/live-run/recent-runs
 * screen (docs/FRONTEND_PLAN.md).
 */
async function checkBackend(): Promise<boolean> {
  try {
    const health = await getHealth();
    return health.status === "ok";
  } catch {
    return false;
  }
}

/** Recent runs, or `null` if the list endpoint itself couldn't be reached -
 * kept distinct from "zero runs" so the section can show a useful message
 * either way (docs/FRONTEND_PLAN.md's Phase 9D instruction 5) instead of
 * conflating "nothing processed yet" with "couldn't load anything". */
async function loadRecentRuns(): Promise<RunSummary[] | null> {
  try {
    const { runs } = await listRuns(20);
    return runs;
  } catch {
    return null;
  }
}

function formatCreatedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Every doc_type except "skipped" is an invoice by construction - the
 * router sends anything else straight to END before doc_type is ever set
 * on the run itself (see app/service.py's derive_status), so "skipped" is
 * the only status this needs to actually read doc_type for. */
function docTypeLabel(run: RunSummary): string {
  return run.status === "skipped" ? (run.doc_type ?? "not an invoice") : "Invoice";
}

function RecentRuns({ runs }: { runs: RunSummary[] | null }) {
  if (runs === null) {
    return <p className="text-sm text-text-muted">Couldn&apos;t load recent runs.</p>;
  }
  if (runs.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No invoices processed yet - upload one above and it will show up here.
      </p>
    );
  }
  return (
    <div className="divide-y divide-border rounded-lg border border-border bg-surface">
      {runs.map((run) => (
        <Link
          key={run.thread_id}
          href={`/runs/${run.thread_id}`}
          className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 px-6 py-3 text-sm hover:bg-brand-soft"
        >
          <RunStatusDot status={run.status} />
          <span className="text-text-muted">{docTypeLabel(run)}</span>
          <span className="font-mono text-text-muted">{run.thread_id}</span>
          <span className="text-text-muted">{formatCreatedAt(run.created_at)}</span>
        </Link>
      ))}
    </div>
  );
}

export default async function Home() {
  const [backendOk, recentRuns] = await Promise.all([checkBackend(), loadRecentRuns()]);

  return (
    <div className="flex flex-col gap-10">
      <p className="max-w-prose text-lg text-text-muted">{PRODUCT_TAGLINE}</p>

      <UploadZone />

      {!backendOk && (
        <div className="rounded-lg border border-border bg-surface p-6">
          <p className="text-sm text-text-muted">
            Backend ({API_BASE_URL}): <span className="text-error">unreachable</span>
          </p>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <h2 className="font-serif text-xl text-text">Recent runs</h2>
        <RecentRuns runs={recentRuns} />
      </div>
    </div>
  );
}
