"use client";

import { useEffect, useReducer, useRef } from "react";
import { ApiError, createRun, getRun, subscribeToRun } from "@/lib/api";
import { claimRunFile, getRunFile } from "@/lib/run-file-cache";
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

interface State {
  run: RunResponse | null;
  stage: { node: string; stage: string } | null;
  fields: Partial<Invoice>;
  receivedFields: Set<string>;
  revealedChecks: CheckEvent[];
  settlingChecks: boolean;
  ledgerStatus: "unknown" | "written" | "failed";
  error: string | null;
}

type Action =
  | { type: "stage"; node: string; stage: string }
  | { type: "field"; name: string; value: unknown }
  | { type: "validation_start" }
  | { type: "check_queued" }
  | { type: "reveal_check"; event: CheckEvent }
  | { type: "checks_settled" }
  | { type: "ledger"; status: "written" | "failed" }
  | { type: "run"; run: RunResponse }
  | { type: "error"; message: string };

const initialState: State = {
  run: null,
  stage: null,
  fields: {},
  receivedFields: new Set(),
  revealedChecks: [],
  settlingChecks: false,
  ledgerStatus: "unknown",
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
      return { ...state, revealedChecks: [], settlingChecks: false };
    case "check_queued":
      return { ...state, settlingChecks: true };
    case "reveal_check":
      return { ...state, revealedChecks: [...state.revealedChecks, action.event] };
    case "checks_settled":
      return { ...state, settlingChecks: false };
    case "ledger":
      return { ...state, ledgerStatus: action.status };
    case "run":
      return { ...state, run: action.run, error: null };
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
 * - a cached File this tab reserved and hasn't uploaded yet (claimRunFile()
 *   returns it at most once per thread_id, even across a remount - see
 *   lib/run-file-cache.ts): fires the real POST /invoices call and uses
 *   its resolved response as ground truth once the blocking call returns.
 * - no claimable file (a reload, a bookmark, a link from elsewhere, or a
 *   remount of a run whose upload another instance already claimed):
 *   fetches current status directly instead, and only opens the SSE
 *   stream if that status isn't already terminal - a finished run's
 *   channel is closed server-side, so a stream would only cost a
 *   handshake for a one-shot snapshot the GET just gave us anyway.
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

  useEffect(() => {
    let cancelled = false;
    let closeStream: (() => void) | null = null;

    const stopPolling = () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    const startPolling = () => {
      if (pollRef.current !== null) return;
      let inFlight = false;
      pollRef.current = setInterval(async () => {
        // Skip this tick rather than let a second GET pile up if the
        // previous one hasn't resolved yet - without this, two overlapping
        // requests could resolve out of order and let a stale response
        // transiently overwrite newer state.
        if (inFlight) return;
        inFlight = true;
        try {
          const run = await getRun(threadId);
          dispatch({ type: "run", run });
          if (isTerminal(run.status)) stopPolling();
        } catch {
          // Transient - keep polling. A run that's genuinely gone (bad
          // thread_id) would 404 forever here, but this hook is only ever
          // mounted with a thread_id this tab just reserved or navigated
          // to, so that's not a case worth a special UI for yet.
        } finally {
          inFlight = false;
        }
      }, POLL_INTERVAL_MS);
    };

    // Client-side stagger for revealing check results (~200ms apart, per
    // docs/FRONTEND_PLAN.md - validation itself runs in milliseconds
    // server-side, never delayed there). Kept as a plain queue + timer
    // here rather than reducer state driving a useEffect dependency: a
    // burst of checks arriving within milliseconds of each other must
    // arm exactly one timer, not re-arm on every arrival - a dependency
    // on queue *length* changes on both an arrival and a reveal, so a
    // fast burst would keep cancelling and re-arming the same timer,
    // delaying the first reveal until after the whole burst instead of
    // ~200ms after the first check.
    const checkQueue: CheckEvent[] = [];
    let revealTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReveal = () => {
      if (revealTimer !== null || checkQueue.length === 0) return;
      revealTimer = setTimeout(() => {
        revealTimer = null;
        const next = checkQueue.shift();
        if (next) dispatch({ type: "reveal_check", event: next });
        if (checkQueue.length === 0) {
          dispatch({ type: "checks_settled" });
        } else {
          scheduleReveal();
        }
      }, CHECK_REVEAL_INTERVAL_MS);
    };

    const onEvent = (event: RunEvent) => {
      switch (event.type) {
        case "stage":
          dispatch({ type: "stage", node: event.node, stage: event.stage });
          break;
        case "field":
          dispatch({ type: "field", name: event.name, value: event.value });
          break;
        case "validation_start":
          checkQueue.length = 0;
          if (revealTimer !== null) {
            clearTimeout(revealTimer);
            revealTimer = null;
          }
          dispatch({ type: "validation_start" });
          break;
        case "check":
          checkQueue.push(event);
          dispatch({ type: "check_queued" });
          scheduleReveal();
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

    const openStream = () => {
      closeStream = subscribeToRun(threadId, onEvent, onTransportError);
    };

    const file = claimRunFile(threadId);
    if (file) {
      openStream();
      createRun(file, threadId)
        .then((run) => dispatch({ type: "run", run }))
        .catch((err) => {
          dispatch({ type: "error", message: err instanceof ApiError ? err.message : "Upload failed." });
          startPolling();
        });
    } else {
      // No file to claim doesn't necessarily mean "genuine revisit of an
      // old run" - under React StrictMode's dev-only double-invoke (mount,
      // cleanup, remount), the *first* mount already claimed it and is
      // uploading right now, and this second mount is racing getRun()
      // against a run whose first checkpoint may not exist yet (a 404).
      // getRunFile() (the non-consuming read) tells the two cases apart:
      // if a file is still sitting there, this thread genuinely has an
      // upload in flight from this tab, and must get a live view - and
      // must not incorrectly skip it (fix for "SSE handshake on revisit
      // wastes a stream") the way a true revisit safely can.
      const hasInFlightUpload = getRunFile(threadId) !== undefined;
      getRun(threadId)
        .then((run) => {
          dispatch({ type: "run", run });
          if (!cancelled && (hasInFlightUpload || !isTerminal(run.status))) {
            openStream();
          }
        })
        .catch(() => {
          dispatch({ type: "error", message: "Couldn't find that run." });
          if (!cancelled && hasInFlightUpload) {
            openStream();
          }
        });
    }

    return () => {
      cancelled = true;
      if (revealTimer !== null) clearTimeout(revealTimer);
      closeStream?.();
      stopPolling();
    };
  }, [threadId]);

  return {
    run: state.run,
    stage: state.stage,
    fields: state.fields,
    receivedFields: state.receivedFields,
    checks: state.revealedChecks,
    checksSettling: state.settlingChecks,
    ledgerStatus: state.ledgerStatus,
    error: state.error,
  };
}
