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

See docs/observability.md for what a trace looks like end to end and why
this two-part approach, rather than switching to langchain_anthropic.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(
        os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_langchain_handler(thread_id: str | None = None):
    """A Langfuse CallbackHandler for `graph.invoke(config={"callbacks": [...]})`,
    or None if tracing isn't configured.

    Pass the graph's `thread_id` when the run might interrupt (e.g. a
    flagged invoice): `interrupt()`/`Command(resume=...)` means the "before"
    and "after" halves of one logical run are two separate `graph.invoke()`
    calls, each building its own callback handler. Without a shared trace
    id they'd land in Langfuse as two disconnected traces. Deriving the
    trace id deterministically from `thread_id` (same seed -> same id)
    merges them into one, so a flagged invoice still shows as a single
    trace with the pause/resume visible in it, not two fragments.
    """
    if not tracing_enabled():
        return None
    from langfuse import get_client
    from langfuse.langchain import CallbackHandler

    if thread_id is None:
        return CallbackHandler()
    trace_id = get_client().create_trace_id(seed=thread_id)
    return CallbackHandler(trace_context={"trace_id": trace_id})


def trace_callbacks(thread_id: str | None = None) -> list:
    """Convenience for building `graph.invoke()`'s config: an empty list if
    tracing isn't configured, so call sites don't need their own None-check."""
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


class _NoopGeneration:
    def record(self, *, output: Any = None, usage: Any = None) -> None:
        pass


@contextmanager
def traced_generation(name: str, *, model: str, input_data: Any = None):
    """Records a raw Anthropic API call as a Langfuse "generation"
    observation, nested under the current active span (the enclosing
    LangGraph node span, when called during a traced `graph.invoke()`). A
    no-op if tracing isn't configured.

    Usage:
        with traced_generation("router-classify", model=ROUTER_MODEL, input_data={...}) as gen:
            response = client.messages.parse(...)
            gen.record(output=response.parsed_output.model_dump(), usage=response.usage)
    """
    if not tracing_enabled():
        yield _NoopGeneration()
        return

    from langfuse import get_client

    client = get_client()
    with client.start_as_current_observation(
        name=name, as_type="generation", model=model, input=input_data
    ) as generation:

        class _Recorder:
            def record(self, *, output: Any = None, usage: Any = None) -> None:
                generation.update(output=output, usage_details=usage_details_from_anthropic(usage))

        yield _Recorder()


def flush() -> None:
    """Force-send any buffered traces before a short-lived script exits. A
    no-op if tracing isn't configured."""
    if not tracing_enabled():
        return
    from langfuse import get_client

    get_client().flush()
