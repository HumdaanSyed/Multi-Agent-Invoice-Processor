import { Calculator, Check, Database, Loader2, ScanSearch, ScanText, UserCheck, X } from "lucide-react";
import type { TimelineMark } from "@/hooks/use-run";
import type { CheckEvent, RunResponse } from "@/lib/types";

export type StepId = "classify" | "extract" | "validate" | "review" | "save";
export type StepStatus = "pending" | "running" | "waiting" | "done" | "skipped" | "error";
export interface StepState {
  status: StepStatus;
  ms?: number;
  detail?: string;
}

export const STEPS: { id: StepId; label: string; where: "Claude" | "Server" | "You"; blurb: string; icon: typeof ScanText }[] = [
  { id: "classify", label: "Classify document", where: "Claude", blurb: "Invoice, receipt, or something else?", icon: ScanSearch },
  { id: "extract", label: "Extract fields", where: "Claude", blurb: "Every field and line item, read live", icon: ScanText },
  { id: "validate", label: "Validate numbers", where: "Server", blurb: "Totals, dates and duplicates, in plain code", icon: Calculator },
  { id: "review", label: "Human review", where: "You", blurb: "Only when a check fails", icon: UserCheck },
  { id: "save", label: "Save & add to ledger", where: "Server", blurb: "Stored in Supabase, exportable as CSV", icon: Database },
];

/** Which stage event starts which step (invoice_agent/graph.py's _publish calls). */
const STARTS: Record<string, StepId> = {
  classifying: "classify",
  extracting: "extract",
  validating: "validate",
  awaiting_review: "review",
  saving: "save",
};

const NODE_STEP: Record<string, StepId> = {
  router: "classify",
  extractor: "extract",
  validator: "validate",
  human_review: "review",
  output: "save",
};

interface Span {
  start: number;
  end?: number;
}

/**
 * Turns the stream's timestamped milestones into one span per step. Every
 * duration is the server's own clock (each event carries `ts`), so a run
 * replayed after a reload shows the same real timings. A resumed run
 * republishes awaiting_review as human_review re-enters, so an already-open
 * review keeps its original start - its duration is how long a person took.
 */
function spansFrom(timeline: TimelineMark[]): Partial<Record<StepId, Span>> {
  const spans: Partial<Record<StepId, Span>> = {};
  let open: StepId | null = null;
  const close = (ts: number) => {
    const span = open ? spans[open] : undefined;
    if (span && span.end === undefined) span.end = ts;
    open = null;
  };
  for (const { kind, ts } of timeline) {
    const starts = STARTS[kind];
    if (starts) {
      if (starts === "review" && open === "review") continue;
      close(ts);
      spans[starts] = { start: ts };
      open = starts;
    } else if (kind === "review_applied") {
      if (open === "review") close(ts);
    } else if (kind === "ledger" || kind === "run_end") {
      if (open !== "review") close(ts);
    }
  }
  return spans;
}

export interface PipelineInput {
  timeline: TimelineMark[];
  run: RunResponse | null;
  checks: CheckEvent[];
  receivedFields: number;
  ledgerStatus: "unknown" | "written" | "failed";
}

export function derivePipeline({ timeline, run, checks, receivedFields, ledgerStatus }: PipelineInput) {
  const spans = spansFrom(timeline);
  const status = run?.status;
  const failedStep = status === "failed" && run?.failed_at_node ? NODE_STEP[run.failed_at_node] : undefined;
  const failedIndex = failedStep ? STEPS.findIndex((s) => s.id === failedStep) : -1;
  const reviewStepIndex = STEPS.findIndex((s) => s.id === "review");
  const terminal = status === "completed" || status === "needs_review" || status === "skipped" || status === "failed";

  const passed = checks.filter((c) => c.passed && !c.skipped).length;
  const applicable = checks.filter((c) => !c.skipped).length;
  const invoice = run?.invoice;
  const ledger = ledgerStatus !== "unknown" ? ledgerStatus : run?.ledger_status;

  const state = {} as Record<StepId, StepState>;
  STEPS.forEach(({ id }, i) => {
    const span = spans[id];
    let s: StepState;
    if (span) {
      s = span.end !== undefined ? { status: "done", ms: Math.max(0, span.end - span.start) } : { status: id === "review" ? "waiting" : "running" };
    } else {
      s = { status: "pending" };
    }

    // The stream only covers what this tab saw; the run's own status fills
    // in the rest (a cold revisit has no timeline at all) - without timings.
    if (status === "skipped") {
      s = i === 0 ? { ...s, status: "done" } : { status: "skipped", detail: "Not an invoice" };
    } else if (failedIndex >= 0) {
      if (i < failedIndex && s.status !== "done") s = { ...s, status: i === reviewStepIndex ? "skipped" : "done" };
      if (i === failedIndex) s = { ...s, status: "error", detail: "Failed here - see the server log" };
    } else if (status === "needs_review") {
      if (i < reviewStepIndex && s.status === "pending") s = { status: "done" };
      if (id === "review" && s.status !== "done") s = { ...s, status: "waiting" };
    } else if (status === "completed") {
      if (id === "review" && !span) {
        s = { status: "skipped", detail: timeline.length ? "Not needed - every check passed" : "No live record for this run" };
      } else if (s.status !== "done") {
        s = { ...s, status: "done" };
      }
    }

    if (!s.detail) {
      if (s.status === "running") s.detail = "working…";
      else if (s.status === "waiting") s.detail = "Waiting for your corrections";
      else if (s.status === "done") {
        if (id === "classify") s.detail = "Invoice detected";
        if (id === "extract") {
          s.detail = invoice
            ? `${invoice.line_items.length} line items · total ${invoice.total.toFixed(2)} ${invoice.currency}`
            : `${receivedFields} fields read`;
        }
        if (id === "validate" && applicable) s.detail = `${passed}/${applicable} checks passed`;
        if (id === "review") s.detail = "Corrections applied and re-checked";
        if (id === "save") s.detail = ledger === "written" ? "Saved · added to ledger" : ledger === "failed" ? "Saved · ledger write failed" : "Saved";
      }
    }
    if (status === "skipped" && i === 0) s.detail = `Classified as ${run?.doc_type ?? "not an invoice"}`;
    state[id] = s;
  });

  const finished = STEPS.filter(({ id }) => state[id].status === "done" || state[id].status === "skipped").length;
  const workMs = STEPS.filter(({ id }) => id !== "review").reduce((sum, { id }) => sum + (state[id].ms ?? 0), 0);
  return { state, finished, workMs: terminal && workMs > 0 ? workMs : null };
}

const RING: Record<StepStatus, string> = {
  pending: "border-slate-200 bg-white text-slate-400 dark:border-slate-700 dark:bg-slate-900",
  running: "border-teal-500 bg-white text-teal-500 shadow-[0_0_0_4px] shadow-teal-500/15 dark:bg-slate-900",
  waiting: "border-amber-500 bg-white text-amber-500 shadow-[0_0_0_4px] shadow-amber-500/15 dark:bg-slate-900",
  done: "border-emerald-500 bg-emerald-500 text-white",
  skipped: "border-slate-300 bg-slate-100 text-slate-400 dark:border-slate-700 dark:bg-slate-800",
  error: "border-rose-500 bg-rose-500 text-white",
};

export const formatMs = (ms: number) =>
  ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;

export function PipelineSteps({ state, finished }: { state: Record<StepId, StepState>; finished: number }) {
  return (
    <div data-testid="pipeline">
      <div className="mb-4 flex items-center justify-between text-sm">
        <h2 className="font-semibold">Processing pipeline</h2>
        <span className="tabular-nums text-slate-500">
          {finished}/{STEPS.length} steps
        </span>
      </div>
      <div className="mb-5 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-teal-500 to-cyan-600 transition-all duration-500"
          style={{ width: `${(finished / STEPS.length) * 100}%` }}
        />
      </div>
      <ol className="space-y-4">
        {STEPS.map(({ id, label, where, icon: Icon }, i) => {
          const s = state[id];
          const inactive = s.status === "pending" || s.status === "skipped";
          return (
            <li key={id} className="relative flex gap-3" data-testid={`step-${id}`} data-status={s.status}>
              {i < STEPS.length - 1 && (
                <span
                  className={`absolute left-[15px] top-8 h-[calc(100%-8px)] w-0.5 ${s.status === "done" ? "bg-emerald-500" : "bg-slate-200 dark:bg-slate-700"}`}
                  aria-hidden
                />
              )}
              <span className={`relative z-10 grid size-8 shrink-0 place-items-center rounded-full border-2 transition ${RING[s.status]}`}>
                {s.status === "running" ? (
                  <Loader2 className="size-4 animate-spin" aria-label="running" />
                ) : s.status === "done" ? (
                  <Check className="size-4" aria-label="done" />
                ) : s.status === "error" ? (
                  <X className="size-4" aria-label="failed" />
                ) : (
                  <Icon className="size-4" aria-hidden />
                )}
              </span>
              <div className="min-w-0 flex-1 pt-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`font-medium ${inactive ? "text-slate-400" : ""}`}>{label}</span>
                  {s.ms != null && <span className="font-mono text-xs tabular-nums text-slate-500">{formatMs(s.ms)}</span>}
                </div>
                <div className="text-xs text-slate-500">
                  <span className="mr-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-medium dark:bg-slate-800">{where}</span>
                  {s.detail}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
