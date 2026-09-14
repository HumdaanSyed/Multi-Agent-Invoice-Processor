import { getHealth } from "@/lib/api";
import { API_BASE_URL, PRODUCT_TAGLINE } from "@/lib/config";

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

export default async function Home() {
  const backendOk = await checkBackend();

  return (
    <div className="flex flex-col gap-10">
      <p className="max-w-prose text-lg text-text-muted">{PRODUCT_TAGLINE}</p>

      <div className="rounded-lg border border-border bg-surface p-6">
        <p className="text-sm text-text-muted">
          Backend ({API_BASE_URL}):{" "}
          <span className={backendOk ? "text-ok" : "text-error"}>
            {backendOk ? "connected" : "unreachable"}
          </span>
        </p>
      </div>
    </div>
  );
}
