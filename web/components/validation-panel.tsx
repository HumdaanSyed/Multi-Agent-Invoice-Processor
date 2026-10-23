import { CheckCircle2, CircleDashed, MinusCircle, XCircle } from "lucide-react";
import { Chip, ScoreBar } from "@/components/primitives";
import type { CheckEvent, CheckId } from "@/lib/types";

/**
 * The fixed set of checks every run publishes, in emission order - mirrors
 * invoice_agent/validate.py. check_id is a stable contract (docs/api.md), so a
 * row can show a pending state before its result arrives. If validate.py adds,
 * removes or renames a check, this list needs the matching update.
 */
const EXPECTED_CHECKS: { check_id: CheckId; label: string }[] = [
  { check_id: "line_items_sum", label: "Line items sum to subtotal" },
  { check_id: "totals_match", label: "Subtotal plus tax equals total" },
  { check_id: "invoice_date_valid", label: "Invoice date is a valid date" },
  { check_id: "due_date_valid", label: "Due date is a valid date" },
  { check_id: "due_date_after_invoice_date", label: "Due date is on or after invoice date" },
  { check_id: "not_duplicate", label: "Not a duplicate of an existing invoice" },
];

type RowState = "pending" | "passed" | "failed" | "skipped";

const STYLE: Record<RowState, { icon: typeof CheckCircle2; color: string }> = {
  passed: { icon: CheckCircle2, color: "text-emerald-500" },
  failed: { icon: XCircle, color: "text-rose-500" },
  skipped: { icon: MinusCircle, color: "text-slate-400" },
  pending: { icon: CircleDashed, color: "text-slate-300 dark:text-slate-600" },
};

/**
 * One row per expected check, in fixed order. `checks` holds only results
 * already revealed (hooks/use-run.ts staggers them ~200ms apart client-side),
 * so lookups are by check_id, not position.
 */
export function ValidationPanel({ checks, settling, bare = false }: { checks: CheckEvent[]; settling: boolean; bare?: boolean }) {
  if (checks.length === 0 && !settling) return null;

  const passed = checks.filter((c) => c.passed && !c.skipped).length;
  const failed = checks.filter((c) => !c.passed && !c.skipped).length;
  const skipped = checks.filter((c) => c.skipped).length;
  const applicable = passed + failed;

  const list = (
    <ul className="grid gap-x-6 sm:grid-cols-2" data-testid="checks">
      {EXPECTED_CHECKS.map(({ check_id, label }) => {
        const check = checks.find((c) => c.check_id === check_id) ?? null;
        const state: RowState = !check ? "pending" : check.skipped ? "skipped" : check.passed ? "passed" : "failed";
        const { icon: Icon, color } = STYLE[state];
        return (
          <li
            key={`${check_id}-${state}`}
            className={`flex items-start gap-3 border-b border-slate-100 py-2.5 dark:border-slate-800 ${state === "pending" ? "" : "animate-[fade-in_0.3s_ease-out_both]"}`}
            data-testid={`check-${check_id}`}
            data-status={state}
          >
            <Icon className={`mt-0.5 size-5 shrink-0 ${color}`} aria-label={state} />
            <div className="min-w-0">
              <div className={`text-sm font-medium ${state === "pending" || state === "skipped" ? "text-slate-400" : ""}`}>
                {check?.label ?? label}
              </div>
              <div className={`break-words text-xs ${state === "failed" ? "text-rose-600 dark:text-rose-400" : "text-slate-500"}`}>
                {state === "failed"
                  ? (check?.detail ?? "Failed")
                  : state === "skipped"
                    ? "Not applicable"
                    : state === "passed"
                      ? "Passed"
                      : "Waiting…"}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );

  const header = (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-3">
        <h2 className="font-semibold">Validation checks</h2>
        {applicable > 0 && <ScoreBar value={passed / applicable} label={`${passed} of ${applicable} checks passed`} />}
      </div>
      <div className="flex gap-2">
        <Chip tone="success">{passed} passed</Chip>
        <Chip tone="error">{failed} failed</Chip>
        {skipped > 0 && <Chip tone="inactive">{skipped} skipped</Chip>}
      </div>
    </div>
  );

  if (bare) {
    return (
      <div>
        {header}
        {list}
      </div>
    );
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      {header}
      {list}
    </section>
  );
}
