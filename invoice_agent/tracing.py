"""Langfuse tracing - optional. If LANGFUSE_PUBLIC_KEY/SECRET_KEY aren't
set in the environment, every function here degrades to a no-op, so
tracing is never a hard requirement for the pipeline to run (matches the
project's existing pattern for optional integrations, e.g. `db.is_duplicate`
defaulting to skipped in tests).

Two things get traced, together forming one tree per run:
  - The LangGraph node tree (Router -> Extractor -> Validator -> Output),
    via `langfuse.langchain.CallbackHandler` passed in `graph.invoke()`'s
    `config`. LangGraph node execution goes through LangChain's Runnable
    protocol, so this alone captures node-level spans automatically.
  - Token usage for the raw Anthropic SDK calls inside the router and
    extractor nodes. Those calls use the plain `anthropic` client, not
    `langchain_anthropic` (see invoice_agent/extract.py's docstring on why),
    so they are NOT auto-captured by the CallbackHandler above -
    `traced_generation()` manually records them as a nested "generation"
    observation under the current node's span.

Every Langfuse SDK call in this module is wrapped so a Langfuse-side
failure (bad credentials, an unreachable host, the wrong region) degrades
to a no-op instead of propagating into the business logic being observed -
tracing must never be able to break invoice processing.

See docs/observability.md for what a trace looks like end to end and why
this two-part approach, rather than switching to langchain_anthropic.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(
        os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_langchain_handler(thread_id: str | None = None):
    """A Langfuse CallbackHandler for `graph.invoke(config={"callbacks": [...]})`,
    or None if tracing isn't configured, or if Langfuse itself fails to
    initialize (bad credentials, unreachable host) - either way this
    returns None rather than raising, so a Langfuse-side problem is never
    treated as fatal by callers.

    Pass the graph's `thread_id` when the run might interrupt (e.g. a
    flagged invoice): `interrupt()`/`Command(resume=...)` means the "before"
    and "after" halves of one logical run are two separate `graph.invoke()`
    calls, each building its own callback handler. Without a shared trace
    id they'd land in Langfuse as two disconnected traces. Deriving the
    trace id deterministically from `thread_id` (same seed -> same id)
    merges them into one, so a flagged invoice still shows as a single
    trace with the pause/resume visible in it, not two fragments.

    This assumes `thread_id` is unique per logical run - true for every
    caller today (each mints a fresh `uuid.uuid4()`), but not enforced
    here. A future caller that ever reuses a `thread_id` across two
    unrelated runs would have those silently merge into one Langfuse trace.
    """
    if not tracing_enabled():
        return None
    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        if thread_id is None:
            return CallbackHandler()
        trace_id = get_client().create_trace_id(seed=thread_id)
        return CallbackHandler(trace_context={"trace_id": trace_id})
    except Exception:
        return None


def trace_callbacks(thread_id: str | None = None) -> list:
    """Convenience for building `graph.invoke()`'s config: an empty list if
    tracing isn't configured (or failed to initialize), so call sites
    don't need their own None-check."""
    handler = get_langchain_handler(thread_id)
    return [handler] if handler is not None else []


def usage_details_from_anthropic(usage: Any) -> dict[str, int]:
    """Anthropic's `Usage` object -> Langfuse's `usage_details` dict, using
    Anthropic's own field names (Langfuse's Anthropic integration
    recognizes this shape natively)."""
    if usage is None:
        return {}
    details = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    if getattr(usage, "cache_creation_input_tokens", None):
        details["cache_creation_input_tokens"] = usage.cache_creation_input_tokens
    if getattr(usage, "cache_read_input_tokens", None):
        details["cache_read_input_tokens"] = usage.cache_read_input_tokens
    return details


@dataclass
class _GenerationRecorder:
    """Yielded by `traced_generation()`. `generation` is None whenever
    tracing is disabled or failed to initialize, in which case `record()`
    is a no-op - callers never need to check which case they're in."""

    generation: Any = None

    def record(self, *, output: Any = None, usage: Any = None) -> None:
        if self.generation is None:
            return
        try:
            self.generation.update(output=output, usage_details=usage_details_from_anthropic(usage))
        except Exception:
            pass  # a Langfuse-side failure here must not surface to the caller


@contextmanager
def traced_generation(name: str, *, model: str, input_data: Any = None):
    """Records a raw Anthropic API call as a Langfuse "generation"
    observation, nested under the current active span (the enclosing
    LangGraph node span, when called during a traced `graph.invoke()`).

    A no-op if tracing isn't configured, AND if Langfuse itself fails to
    open the observation (bad credentials, unreachable host, wrong
    region) - either way the wrapped code in the `with` block still runs
    normally. Tracing must never be able to prevent the Anthropic call it
    wraps from happening.

    Usage:
        with traced_generation("router-classify", model=ROUTER_MODEL, input_data={...}) as gen:
            response = client.messages.parse(...)
            gen.record(output=response.parsed_output.model_dump(), usage=response.usage)
    """
    if not tracing_enabled():
        yield _GenerationRecorder()
        return

    try:
        from langfuse import get_client

        observation_cm = get_client().start_as_current_observation(
            name=name, as_type="generation", model=model, input=input_data
        )
        generation = observation_cm.__enter__()
    except Exception:
        yield _GenerationRecorder()
        return

    try:
        yield _GenerationRecorder(generation)
    except BaseException as exc:
        try:
            observation_cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            observation_cm.__exit__(None, None, None)
        except Exception:
            pass


def flush() -> None:
    """Force-send any buffered traces before a short-lived script exits. A
    no-op if tracing isn't configured, or if the flush itself fails - by
    this point the invoice has already been fully processed and persisted,
    so losing buffered trace data is a strictly smaller problem than
    reporting a successful run as a failure to whatever's watching the
    exit code."""
    if not tracing_enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        pass
