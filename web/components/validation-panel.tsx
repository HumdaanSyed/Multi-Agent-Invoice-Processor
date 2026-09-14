import { Check, Minus, X } from "lucide-react";
import type { CheckEvent } from "@/lib/types";

function CheckRow({ check }: { check: CheckEvent }) {
  const state = check.skipped ? "skipped" : check.passed ? "passed" : "failed";

  const icon =
    state === "passed" ? (
      <Check className="size-4 text-ok" aria-hidden />
    ) : state === "failed" ? (
      <X className="size-4 text-error" aria-hidden />
    ) : (
      <Minus className="size-4 text-text-muted" aria-hidden />
    );

  return (
    <div className="flex flex-col gap-0.5 py-2 animate-in fade-in duration-300">
      <div className="flex items-center gap-2">
        {icon}
        <span className={state === "skipped" ? "text-sm text-text-muted" : "text-sm text-text"}>
          {check.label}
        </span>
      </div>
      {state === "failed" && check.detail && (
        <p className="pl-6 text-sm text-error">{check.detail}</p>
      )}
      {state === "skipped" && <p className="pl-6 text-sm text-text-muted">Not applicable</p>}
    </div>
  );
}

/**
 * Checks resolve from the backend in a few milliseconds - useRun() already
 * staggers revealing them client-side (~200ms apart) so this just renders
 * whatever's been revealed so far, in that order. `settling` shows a
 * pending hint while more are still queued.
 */
export function ValidationPanel({ checks, settling }: { checks: CheckEvent[]; settling: boolean }) {
  if (checks.length === 0 && !settling) return null;

  return (
    <div className="rounded-lg border border-border bg-surface p-6">
      <p className="mb-2 text-sm text-text-muted">Validation</p>
      <div className="divide-y divide-border">
        {checks.map((check) => (
          <CheckRow key={check.check_id} check={check} />
        ))}
      </div>
      {settling && <p className="pt-2 text-sm text-text-muted">Checking…</p>}
    </div>
  );
}
