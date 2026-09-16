import { Check, Minus, X } from "lucide-react";
import type { CheckEvent, CheckId } from "@/lib/types";

/**
 * The fixed set of checks every run publishes, in emission order - mirrors
 * invoice_agent/validate.py's `rows` table. check_id is documented
 * (docs/api.md) as a stable string "safe to key UI state/ordering off" -
 * this is exactly that: it lets a check's row (with its real label) exist
 * and show a neutral pending state before the check has actually resolved,
 * per docs/FRONTEND_PLAN.md's Phase 9B instruction 4. If validate.py adds,
 * removes, or renames a check, this list needs the matching update.
 */
const EXPECTED_CHECKS: { check_id: CheckId; label: string }[] = [
  { check_id: "line_items_sum", label: "Line items sum to subtotal" },
  { check_id: "totals_match", label: "Subtotal plus tax equals total" },
  { check_id: "invoice_date_valid", label: "Invoice date is a valid date" },
  { check_id: "due_date_valid", label: "Due date is a valid date" },
  { check_id: "due_date_after_invoice_date", label: "Due date is on or after invoice date" },
  { check_id: "not_duplicate", label: "Not a duplicate of an existing invoice" },
];

function CheckRow({ label, check }: { label: string; check: CheckEvent | null }) {
  const state = check === null ? "pending" : check.skipped ? "skipped" : check.passed ? "passed" : "failed";

  const icon =
    state === "passed" ? (
      <Check className="size-4 text-ok" aria-hidden />
    ) : state === "failed" ? (
      <X className="size-4 text-error" aria-hidden />
    ) : (
      <Minus className="size-4 text-text-muted" aria-hidden />
    );

  return (
    <div className="flex flex-col gap-0.5 py-2">
      <div className={`flex items-center gap-2 ${state === "pending" ? "" : "animate-in fade-in duration-300"}`}>
        {icon}
        <span className={state === "passed" ? "text-sm text-text" : "text-sm text-text-muted"}>{label}</span>
      </div>
      {state === "failed" && check?.detail && <p className="pl-6 text-sm text-error">{check.detail}</p>}
      {state === "skipped" && <p className="pl-6 text-sm text-text-muted">Not applicable</p>}
    </div>
  );
}

/**
 * Renders one row per EXPECTED_CHECKS entry, in fixed order, from the
 * start of validation - a check without a matching revealed CheckEvent yet
 * shows the neutral pending state (a label with a dash, no color), then
 * resolves to green/red/grey once useRun()'s staggered reveal (~200ms
 * apart) delivers its real result. `checks` only ever contains already-
 * revealed results (see hooks/use-run.ts) so lookups here are by check_id,
 * not by array position.
 */
export function ValidationPanel({ checks, settling }: { checks: CheckEvent[]; settling: boolean }) {
  if (checks.length === 0 && !settling) return null;

  return (
    <div className="rounded-lg border border-border bg-surface p-6">
      <p className="mb-2 text-sm text-text-muted">Validation</p>
      <div className="divide-y divide-border">
        {EXPECTED_CHECKS.map(({ check_id, label }) => {
          const resolved = checks.find((check) => check.check_id === check_id) ?? null;
          return <CheckRow key={check_id} label={resolved?.label ?? label} check={resolved} />;
        })}
      </div>
    </div>
  );
}
