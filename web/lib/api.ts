/**
 * Typed client for the FastAPI backend (docs/api.md). One function per
 * endpoint, plus subscribeToRun() for the SSE stream. Every function throws
 * ApiError on a non-2xx response, parsed from the backend's one error shape
 * (app/models.py's ErrorResponse, see app/errors.py).
 */

import { API_BASE_URL } from "./config";
import type {
  ErrorResponse,
  ExportStatusResponse,
  HealthResponse,
  Invoice,
  InvoiceCorrections,
  ReadinessResponse,
  RunEvent,
  RunEventType,
  RunListResponse,
  RunResponse,
  RunTicket,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly threadId: string | null;
  readonly detail: Record<string, unknown>[] | null;

  constructor(status: number, body: ErrorResponse) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error;
    this.threadId = body.thread_id ?? null;
    this.detail = body.detail ?? null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    // Every error this API raises deliberately (app/errors.py) - plus
    // FastAPI's own validation errors - comes back as this one JSON shape.
    // A response body that isn't JSON (a proxy's HTML error page, a 502
    // from something in front of the backend) is treated as a generic
    // failure instead of throwing a second, more confusing parse error.
    const body: ErrorResponse = await response.json().catch(() => ({
      error: "unknown_error",
      message: `Request failed with status ${response.status}`,
    }));
    throw new ApiError(response.status, body);
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return request("/health");
}

export function getReadiness(): Promise<ReadinessResponse> {
  return request("/health/ready");
}

/** Mint a thread_id and open its SSE channel before any upload exists -
 * call this, open the stream at the returned stream_url, then pass the
 * same thread_id to createRun() so the run's own events are seen live
 * instead of only replayed as history. Optional: createRun() with no
 * threadId still works for a caller that only wants the final answer. */
export function reserveRun(): Promise<RunTicket> {
  return request("/invoices/reserve", { method: "POST" });
}

/** Uploads a PDF and blocks until the run interrupts or completes (see
 * docs/api.md's "Why blocking, not 202 + polling"). Pass `threadId` from a
 * prior reserveRun() call to attach this run to an already-open stream. */
export function createRun(file: File, threadId?: string): Promise<RunResponse> {
  const body = new FormData();
  body.append("file", file);
  if (threadId) {
    body.append("thread_id", threadId);
  }
  return request("/invoices", { method: "POST", body });
}

export function getRun(threadId: string): Promise<RunResponse> {
  return request(`/invoices/${encodeURIComponent(threadId)}`);
}

/** Submits a sparse patch of corrections (or none, to accept as extracted /
 * retry a failed write - see docs/api.md's "The resume contract") and
 * continues the run. */
export function resumeRun(threadId: string, corrections?: InvoiceCorrections): Promise<RunResponse> {
  return request(`/invoices/${encodeURIComponent(threadId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ corrections: corrections ?? {} }),
  });
}

export function listRuns(limit = 20): Promise<RunListResponse> {
  return request(`/invoices?limit=${limit}`);
}

export function getExportStatus(): Promise<ExportStatusResponse> {
  return request("/export/status");
}

/** GET /export.csv streams the whole ledger - point an <a href> at this
 * (or navigate to it) rather than fetching it, so the browser handles the
 * download and Content-Disposition filename itself. */
export function exportCsvUrl(): string {
  return `${API_BASE_URL}/export.csv`;
}

export function streamUrl(threadId: string): string {
  return `${API_BASE_URL}/invoices/${encodeURIComponent(threadId)}/stream`;
}

const RUN_EVENT_TYPES: RunEventType[] = [
  "stage",
  "field",
  "validation_start",
  "check",
  "interrupted",
  "run_end",
  "overflow",
  "snapshot",
  "done",
  "error",
  "ledger",
];

/**
 * Opens `GET /invoices/{thread_id}/stream` and calls `onEvent` for every
 * event as it arrives, typed as the discriminated RunEvent union (the
 * event's SSE `event:` name becomes its `type` field). Returns a cleanup
 * function - call it on unmount or when the run reaches a terminal state,
 * so a component that re-subscribes doesn't leak connections.
 *
 * EventSource has no built-in typed events, so this attaches one listener
 * per known event name (docs/api.md's event table) and re-shapes each into
 * a plain RunEvent object rather than making callers deal with MessageEvent
 * and JSON.parse themselves.
 */
export function subscribeToRun(
  threadId: string,
  onEvent: (event: RunEvent) => void,
  onError?: (event: Event) => void,
): () => void {
  const source = new EventSource(streamUrl(threadId));

  for (const type of RUN_EVENT_TYPES) {
    source.addEventListener(type, (message: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(message.data) as Record<string, unknown>;
        onEvent({ type, ...payload } as RunEvent);
      } catch {
        // A malformed event is dropped rather than thrown - one bad frame
        // shouldn't take down the whole live view.
      }
    });
  }

  if (onError) {
    source.onerror = onError;
  }

  return () => source.close();
}

export type { Invoice, InvoiceCorrections };
