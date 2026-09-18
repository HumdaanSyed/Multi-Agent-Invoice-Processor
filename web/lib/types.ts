/**
 * Data contracts for the FastAPI backend (docs/api.md). Kept as a plain
 * mirror of app/models.py + invoice_agent/schema.py - if a field is added
 * or renamed there, it should be added or renamed here too, not inferred.
 */

// Mirrors invoice_agent/schema.py
export interface LineItem {
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}

export interface Invoice {
  invoice_number: string;
  invoice_date: string;
  vendor_name: string;
  bill_to: string;
  line_items: LineItem[];
  subtotal: number;
  tax: number;
  total: number;
  due_date: string | null;
  currency: string;
}

// Mirrors app/models.py
export type RunStatus = "processing" | "needs_review" | "completed" | "skipped" | "failed";

/** Mirrors invoice_agent/validate.py's ValidationResult/CheckResult -
 * `checks` here is the same shape as the SSE CheckEvent list (minus
 * `type`/`thread_id`), always present for a completed run regardless of
 * source (unlike the streamed events, which only ever arrive for a run
 * this tab watched live) - see components/invoice-detail.tsx. */
export interface ValidationCheckResult {
  check_id: CheckId;
  label: string;
  passed: boolean;
  detail?: string | null;
  skipped?: boolean;
}

export interface ValidationResult {
  passed: boolean;
  flags: string[];
  needs_review: boolean;
  checks: ValidationCheckResult[];
}

export interface RunResponse {
  thread_id: string;
  status: RunStatus;
  doc_type?: string | null;
  invoice?: Invoice | null;
  validation?: ValidationResult | null;
  flags?: string[] | null;
  current_node?: string | null;
  failed_at_node?: string | null;
  /** Populated for a completed run on every endpoint that reaches one
   * (POST /invoices, resume, and GET) - plain GraphState, no extra I/O
   * needed, unlike pdf_url/trace_url below. Durable, so it survives a cold
   * revisit (a reload, a bookmark) unlike the SSE-only "ledger" event. */
  ledger_status?: "written" | "failed" | null;
  /** Only ever set by GET /invoices/{thread_id} for a completed run - see
   * app/models.py's RunResponse docstring. Never present on POST/resume's
   * response, even for the same completed run. */
  pdf_url?: string | null;
  /** Same as pdf_url: completed-detail-fetch only, and only set at all if
   * the backend has LANGFUSE_PROJECT_ID configured (docs/observability.md). */
  trace_url?: string | null;
}

export interface RunSummary {
  thread_id: string;
  status: RunStatus;
  doc_type?: string | null;
  created_at?: string | null;
}

export interface RunListResponse {
  runs: RunSummary[];
}

export interface RunTicket {
  thread_id: string;
  stream_url: string;
}

/** Sparse patch of Invoice fields for POST /invoices/{thread_id}/resume -
 * every field optional, an omitted key keeps the checkpointed value (see
 * docs/api.md's "The resume contract"). */
export type InvoiceCorrections = Partial<Invoice>;

export interface ErrorResponse {
  error: string;
  message: string;
  thread_id?: string | null;
  detail?: Record<string, unknown>[] | null;
}

export interface HealthResponse {
  status: "ok";
}

export interface ReadinessCheck {
  configured: boolean;
  detail?: string | null;
}

export interface ReadinessResponse {
  status: "ok" | "degraded";
  checks: Record<string, ReadinessCheck>;
}

export interface ExportStatusResponse {
  row_count: number;
  last_written_at: string | null;
}

// --- SSE events (docs/api.md's "Streaming" section) ---------------------
//
// Every event carries `thread_id` (invoice_agent/events.py's Event.to_sse
// always includes it) plus whatever fields are specific to its type. The
// wire format is a named SSE event (`event: <type>`) with a JSON `data:`
// payload - see lib/api.ts's subscribeToRun for how these are parsed back
// into this discriminated union.

export interface StageEvent {
  type: "stage";
  thread_id: string;
  node: string;
  stage: string;
}

export interface FieldEvent {
  type: "field";
  thread_id: string;
  name: string;
  value: unknown;
}

export interface ValidationStartEvent {
  type: "validation_start";
  thread_id: string;
}

/** Stable check_id strings (docs/api.md) - safe to key UI state/ordering
 * off, unsafe to rename on the backend without updating this list. */
export type CheckId =
  | "line_items_sum"
  | "totals_match"
  | "invoice_date_valid"
  | "due_date_valid"
  | "due_date_after_invoice_date"
  | "not_duplicate";

export interface CheckEvent {
  type: "check";
  thread_id: string;
  check_id: CheckId;
  label: string;
  passed: boolean;
  detail?: string | null;
  skipped?: boolean;
}

export interface InterruptedEvent {
  type: "interrupted";
  thread_id: string;
  status: "needs_review";
  invoice: Invoice;
  flags: string[];
}

export interface RunEndEvent {
  type: "run_end";
  thread_id: string;
  status: RunStatus;
}

export interface OverflowEvent {
  type: "overflow";
  thread_id: string;
}

export interface SnapshotEvent extends RunResponse {
  type: "snapshot";
}

export interface DoneEvent {
  type: "done";
  thread_id: string;
}

export interface StreamErrorEvent {
  type: "error";
  thread_id: string;
  code: string;
  message: string;
}

/** invoice_agent/graph.py's output() node, fired once a completed run's
 * ledger insert succeeds or fails. A ledger failure never fails the run
 * itself (see REVIEW.md) - the persistent export bar uses this to show
 * that run's ledger status. */
export interface LedgerEvent {
  type: "ledger";
  thread_id: string;
  status: "written" | "failed";
}

export type RunEvent =
  | StageEvent
  | FieldEvent
  | ValidationStartEvent
  | CheckEvent
  | InterruptedEvent
  | RunEndEvent
  | OverflowEvent
  | SnapshotEvent
  | DoneEvent
  | StreamErrorEvent
  | LedgerEvent;

export type RunEventType = RunEvent["type"];
