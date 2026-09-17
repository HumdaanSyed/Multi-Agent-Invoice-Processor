"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { ApiError, createRun, getRun, resumeRun, subscribeToRun } from "@/lib/api";
import { claimRunFile, getRunFile } from "@/lib/run-file-cache";
import type { CheckEvent, Invoice, InvoiceCorrections, RunEvent, RunResponse } from "@/lib/types";

/** SSE event types after which the *server* ends this connection on its
 * own (docs/api.md: run_end and done close the connection and, for
 * run_end, the channel too; interrupted closes only the connection,
 * deliberately, so a resume has somewhere new to open one) - but only once
 * the event is the *current* one, per invoice_agent/events.py's
 * `is_current` bookkeeping, which never reaches the client over the wire.
 * Never used to proactively close the client's own connection inline (see
 * onTransportError below for why) - only consulted reactively there, to
 * tell an expected server-initiated close apart from a real dropped
 * connection. */
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
  resuming: boolean;
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
  | { type: "resuming"; value: boolean }
  | { type: "error"; message: string };

const initialState: State = {
  run: null,
  stage: null,
  fields: {},
  receivedFields: new Set(),
  revealedChecks: [],
  settlingChecks: false,
  ledgerStatus: "unknown",
  resuming: false,
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
    case "resuming":
      return { ...state, resuming: action.value };
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
 *
 * Also returns resume(corrections): submits a correction to the resume
 * endpoint. "interrupted" already closed the previous SSE connection
 * (docs/api.md - deliberately, so a resume has somewhere new to open one),
 * so this opens a fresh one first, same reserve-before-upload ordering as
 * the initial run, so the re-validation's own events aren't missed.
 */
export function useRun(threadId: string) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resumeRef = useRef<((corrections: InvoiceCorrections) => Promise<void>) | null>(null);

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

    // Tracks the most recently processed event's type, purely so
    // onTransportError (below) can tell an expected server-initiated close
    // apart from a genuine dropped connection - see its own comment.
    let lastEventType: RunEvent["type"] | null = null;

    // The last-seen SSE id, kept across every openStream() call within this
    // mount (unlike lastEventType, which resets per connection) - passed
    // back in on the next connection so the server skips replaying history
    // this tab has already processed (events.py's per-run history grows
    // with every resume; the id it needs isn't sent as a header, since
    // EventSource gives no way to set one on a connection this code opens
    // itself - see api.ts's subscribeToRun).
    let lastSeenEventId: string | undefined;

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
      lastEventType = event.type;
    };

    const onTransportError = () => {
      // A run interrupted and later resumed republishes a fresh
      // validation_start/checks/interrupted-or-run_end into the SAME
      // channel history (invoice_agent/events.py) - a *new* connection
      // (opened after a resume, or under React Strict Mode's double-mount)
      // replays that *entire* accumulated history from the start, so an
      // earlier "interrupted" can sit mid-replay with more, newer events
      // still to come after it. The server itself only closes the
      // connection once it reaches the truly *current* terminal event
      // (events.py's is_current bookkeeping) - it deliberately does NOT
      // close right after a stale one, specifically so a client reading
      // the whole stream reaches the real current state. So this code
      // must never proactively close on seeing interrupted/run_end/done/
      // error inline (that raced ahead of the server and truncated the
      // replay before the current state ever arrived - the exact bug that
      // shipped here initially). Instead: EventSource's onerror always
      // fires when the server ends the connection, terminal-event or not,
      // so check what the *last* event we actually saw was. If it was one
      // of STREAM_END_EVENTS, the server closed this on purpose right
      // after delivering the real current state - our own state is
      // already correct, nothing to recover. Otherwise this is a genuine
      // dropped connection mid-stream, and only then does the polling
      // fallback belong.
      const wasExpectedClose = lastEventType !== null && STREAM_END_EVENTS.has(lastEventType);
      closeStream?.();
      if (!wasExpectedClose) {
        startPolling();
      }
    };

    const openStream = () => {
      // Close whatever connection is already open first - resume() can
      // call this while an earlier connection (e.g. one that already
      // received "interrupted" but hasn't had its own onerror fire yet,
      // since that only happens once the browser notices the server ended
      // it) is technically still alive. Leaving it open let its eventual
      // onerror fire against lastEventType/closeStream this fresh call had
      // already reset, sometimes closing the NEW stream instead of the
      // dead old one and falling back to polling mid-resume.
      closeStream?.();
      // Reset so a stale lastEventType from the just-closed connection
      // can't be mistaken for this fresh one's outcome if it fails
      // immediately.
      lastEventType = null;
      closeStream = subscribeToRun(threadId, onEvent, onTransportError, lastSeenEventId, (id) => {
        lastSeenEventId = id;
      });
    };

    resumeRef.current = async (corrections: InvoiceCorrections) => {
      dispatch({ type: "resuming", value: true });
      stopPolling();
      openStream();
      try {
        const run = await resumeRun(threadId, corrections);
        dispatch({ type: "run", run });
      } catch (err) {
        dispatch({ type: "error", message: err instanceof ApiError ? err.message : "Resume failed." });
      } finally {
        dispatch({ type: "resuming", value: false });
      }
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
          // needs_review is "terminal" for isTerminal()'s purpose (stop
          // polling, show the review form) but NOT for the channel - per
          // docs/api.md, interrupted only closes the connection, deliberately
          // leaving the channel open for a resume. A revisit (a reload, a
          // shared link) with no in-flight upload still needs a stream here:
          // ReviewForm's failed-check list/amber fields come entirely from
          // replayed "check" events, which never arrive without one, leaving
          // the review screen blank with no visible reason for review.
          const needsChannel = hasInFlightUpload || run.status === "needs_review" || !isTerminal(run.status);
          if (!cancelled && needsChannel) {
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
      resumeRef.current = null;
      if (revealTimer !== null) clearTimeout(revealTimer);
      closeStream?.();
      stopPolling();
    };
  }, [threadId]);

  const resume = useCallback((corrections: InvoiceCorrections) => {
    return resumeRef.current?.(corrections) ?? Promise.resolve();
  }, []);

  return {
    run: state.run,
    stage: state.stage,
    fields: state.fields,
    receivedFields: state.receivedFields,
    checks: state.revealedChecks,
    checksSettling: state.settlingChecks,
    ledgerStatus: state.ledgerStatus,
    resuming: state.resuming,
    resume,
    error: state.error,
  };
}
