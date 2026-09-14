"use client";

import { useEffect, useReducer, useRef } from "react";
import { ApiError, createRun, getRun, subscribeToRun } from "@/lib/api";
import { getRunFile } from "@/lib/run-file-cache";
import type { CheckEvent, Invoice, RunEvent, RunResponse } from "@/lib/types";

/** SSE event types that mean "this particular stream connection is over" -
 * the server has already closed its end (docs/api.md: run_end and done
 * close the connection and, for run_end, the channel too; interrupted
 * closes only the connection, deliberately, so a resume has somewhere new
 * to open one). Closing our side too stops EventSource's built-in
 * auto-reconnect from firing a pointless reconnect against an already-
 * finished or already-closed channel. */
const STREAM_END_EVENTS = new Set<RunEvent["type"]>(["run_end", "done", "interrupted", "error"]);

const POLL_INTERVAL_MS = 2500;
const CHECK_REVEAL_INTERVAL_MS = 200;

export type ConnectionState = "connecting" | "live" | "polling" | "closed";

interface State {
  run: RunResponse | null;
  stage: { node: string; stage: string } | null;
  fields: Partial<Invoice>;
  receivedFields: Set<string>;
  revealedChecks: CheckEvent[];
  pendingChecks: CheckEvent[];
  ledgerStatus: "unknown" | "written" | "failed";
  connection: ConnectionState;
  error: string | null;
}

type Action =
  | { type: "stage"; node: string; stage: string }
  | { type: "field"; name: string; value: unknown }
  | { type: "validation_start" }
  | { type: "check"; event: CheckEvent }
  | { type: "reveal_next_check" }
  | { type: "ledger"; status: "written" | "failed" }
  | { type: "run"; run: RunResponse }
  | { type: "connection"; value: ConnectionState }
  | { type: "error"; message: string };

const initialState: State = {
  run: null,
  stage: null,
  fields: {},
  receivedFields: new Set(),
  revealedChecks: [],
  pendingChecks: [],
  ledgerStatus: "unknown",
  connection: "connecting",
  error: null,
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "stage":
      return { ...state, stage: { node: action.node, stage: action.stage } };
    case "field": {
      const receivedFields = new Set(state.receivedFields);
      receivedFields.add(action.name);
      return { ...state, fields: { ...state.fields, [action.name]: action.value }, receivedFields };
    }
    case "validation_start":
      // A resume re-enters validation unconditionally, republishing a full
      // new set of checks - discard whatever was shown before and start
      // the reveal sequence over (docs/api.md).
      return { ...state, revealedChecks: [], pendingChecks: [] };
    case "check":
      return { ...state, pendingChecks: [...state.pendingChecks, action.event] };
    case "reveal_next_check": {
      if (state.pendingChecks.length === 0) return state;
      const [next, ...rest] = state.pendingChecks;
      return { ...state, revealedChecks: [...state.revealedChecks, next], pendingChecks: rest };
    }
    case "ledger":
      return { ...state, ledgerStatus: action.status };
    case "run":
      return { ...state, run: action.run, error: null };
    case "connection":
      return { ...state, connection: action.value };
    case "error":
      return { ...state, error: action.message };
    default:
      return state;
  }
}

function isTerminal(status: RunResponse["status"] | undefined): boolean {
  return status === "completed" || status === "skipped" || status === "failed" || status === "needs_review";
}

/**
 * Drives a run's live view: subscribes to its SSE stream for the animated
 * field/check/stage events, and separately tracks the authoritative
 * RunResponse from whichever source actually produces one -
 *
 * - a cached File (this tab just reserved and is uploading it): fires the
 *   real POST /invoices call and uses its resolved response as ground
 *   truth once the blocking call returns.
 * - no cached file (a reload, a bookmark, a link from elsewhere): fetches
 *   current status directly instead.
 *
 * If the SSE connection drops (a proxy hiccup, not a normal server close -
 * see STREAM_END_EVENTS) or the POST itself fails/times out client-side,
 * falls back to polling GET /invoices/{thread_id} so the UI still reaches
 * a correct final state even though createRun()'s own promise may never
 * resolve (docs/api.md: "a timed-out client loses nothing but the response
 * body" - the run keeps going server-side).
 */
export function useRun(threadId: string) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    const stopPolling = () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    const startPolling = () => {
      if (pollRef.current !== null) return;
      dispatch({ type: "connection", value: "polling" });
      pollRef.current = setInterval(async () => {
        try {
          const run = await getRun(threadId);
          dispatch({ type: "run", run });
          if (isTerminal(run.status)) stopPolling();
        } catch {
          // Transient - keep polling. A run that's genuinely gone (bad
          // thread_id) would 404 forever here, but this hook is only ever
          // mounted with a thread_id this tab just reserved or navigated
          // to, so that's not a case worth a special UI for yet.
        }
      }, POLL_INTERVAL_MS);
    };

    let closeStream: (() => void) | null = null;

    const onEvent = (event: RunEvent) => {
      switch (event.type) {
        case "stage":
          dispatch({ type: "stage", node: event.node, stage: event.stage });
          break;
        case "field":
          dispatch({ type: "field", name: event.name, value: event.value });
          break;
        case "validation_start":
          dispatch({ type: "validation_start" });
          break;
        case "check":
          dispatch({ type: "check", event });
          break;
        case "ledger":
          dispatch({ type: "ledger", status: event.status });
          break;
        default:
          break;
      }
      if (STREAM_END_EVENTS.has(event.type)) {
        closeStream?.();
      }
    };

    const onTransportError = () => {
      // EventSource's own error - a dropped connection, not one of the
      // backend's documented terminal event types. Stop relying on it and
      // fall back to polling for the authoritative final state.
      closeStream?.();
      startPolling();
    };

    closeStream = subscribeToRun(threadId, onEvent, onTransportError);
    dispatch({ type: "connection", value: "live" });

    if (!startedRef.current) {
      startedRef.current = true;
      const file = getRunFile(threadId);
      if (file) {
        createRun(file, threadId)
          .then((run) => dispatch({ type: "run", run }))
          .catch((err) => {
            dispatch({ type: "error", message: err instanceof ApiError ? err.message : "Upload failed." });
            startPolling();
          });
      } else {
        getRun(threadId)
          .then((run) => dispatch({ type: "run", run }))
          .catch(() => dispatch({ type: "error", message: "Couldn't find that run." }));
      }
    }

    return () => {
      closeStream?.();
      stopPolling();
    };
  }, [threadId]);

  // Stagger revealing queued check results client-side (~200ms apart) so
  // a viewer can actually read them resolving in sequence - validation
  // itself runs in milliseconds, so without this every check would appear
  // to resolve simultaneously. Never add the delay server-side for this;
  // pacing belongs in the client (docs/FRONTEND_PLAN.md).
  useEffect(() => {
    if (state.pendingChecks.length === 0) return;
    const timer = setTimeout(() => dispatch({ type: "reveal_next_check" }), CHECK_REVEAL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [state.pendingChecks.length]);

  return {
    run: state.run,
    stage: state.stage,
    fields: state.fields,
    receivedFields: state.receivedFields,
    checks: state.revealedChecks,
    checksSettling: state.pendingChecks.length > 0,
    ledgerStatus: state.ledgerStatus,
    connection: state.connection,
    error: state.error,
  };
}
