import type { RunStatus } from "@/lib/types";

/**
 * A small colored dot plus a word - explicitly not a badge/pill
 * (docs/FRONTEND_PLAN.md's Phase 9D instruction 1). Shared between the
 * home screen's recent-runs list and anywhere else a run's terminal-or-not
 * status needs a compact label.
 */
const STATUS_META: Record<RunStatus, { label: string; dotClassName: string }> = {
  processing: { label: "Processing", dotClassName: "bg-text-muted animate-pulse" },
  needs_review: { label: "Needs review", dotClassName: "bg-flag" },
  completed: { label: "Completed", dotClassName: "bg-ok" },
  skipped: { label: "Skipped", dotClassName: "bg-text-muted" },
  failed: { label: "Failed", dotClassName: "bg-error" },
};

export function RunStatusDot({ status }: { status: RunStatus }) {
  const meta = STATUS_META[status];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm text-text">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dotClassName}`} aria-hidden />
      {meta.label}
    </span>
  );
}
