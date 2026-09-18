"""Tests for invoice_agent.tracing - offline, no network calls, no
LANGFUSE_* credentials set. Every path exercised here is the "tracing
disabled" no-op branch; the live-Langfuse-enabled paths (real trace tree,
real token usage, interrupt/resume merging into one trace) were verified
manually against a real Langfuse project during Phase 7 - see
docs/observability.md."""

import pytest

from invoice_agent.tracing import (
    _GenerationRecorder,
    flush,
    get_langchain_handler,
    trace_callbacks,
    trace_url,
    traced_generation,
    tracing_enabled,
    usage_details_from_anthropic,
)


class _FakeUsage:
    def __init__(
        self,
        input_tokens,
        output_tokens,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    ):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


@pytest.fixture(autouse=True)
def _no_langfuse_credentials(monkeypatch):
    """Guarantee every test starts from a known "tracing disabled" state,
    regardless of what the actual shell environment happens to have set."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


# --- tracing_enabled -----------------------------------------------------


def test_tracing_enabled_false_when_unset():
    assert tracing_enabled() is False


def test_tracing_enabled_false_when_only_public_key_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    assert tracing_enabled() is False


def test_tracing_enabled_false_when_only_secret_key_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert tracing_enabled() is False


def test_tracing_enabled_true_when_both_set(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert tracing_enabled() is True


# --- usage_details_from_anthropic -----------------------------------------


def test_usage_details_none_returns_empty_dict():
    assert usage_details_from_anthropic(None) == {}


def test_usage_details_required_fields_only():
    usage = _FakeUsage(input_tokens=100, output_tokens=20)
    assert usage_details_from_anthropic(usage) == {"input_tokens": 100, "output_tokens": 20}


def test_usage_details_includes_cache_fields_when_present():
    usage = _FakeUsage(
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=10,
    )
    assert usage_details_from_anthropic(usage) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 10,
    }


def test_usage_details_omits_falsy_cache_fields():
    usage = _FakeUsage(
        input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=None
    )
    details = usage_details_from_anthropic(usage)
    assert "cache_creation_input_tokens" not in details
    assert "cache_read_input_tokens" not in details


def test_usage_details_missing_cache_attrs_handled():
    """A usage object that doesn't even have the cache attributes (e.g. an
    older SDK version) must not raise via getattr."""

    class _MinimalUsage:
        input_tokens = 5
        output_tokens = 2

    assert usage_details_from_anthropic(_MinimalUsage()) == {"input_tokens": 5, "output_tokens": 2}


# --- disabled-tracing no-op behavior --------------------------------------


def test_get_langchain_handler_none_when_disabled():
    assert get_langchain_handler() is None
    assert get_langchain_handler(thread_id="abc") is None


def test_trace_callbacks_empty_list_when_disabled():
    assert trace_callbacks() == []
    assert trace_callbacks(thread_id="abc") == []


def test_flush_is_noop_when_disabled():
    flush()  # must not raise, must not touch the network


def test_traced_generation_yields_working_noop_recorder():
    with traced_generation("test-span", model="claude-sonnet-5") as gen:
        gen.record(output={"ok": True}, usage=_FakeUsage(1, 1))  # must not raise


def test_traced_generation_propagates_wrapped_exception():
    """Tracing must never swallow an exception from the code it wraps."""
    with pytest.raises(ValueError, match="boom"):
        with traced_generation("test-span", model="claude-sonnet-5"):
            raise ValueError("boom")


# --- _GenerationRecorder ---------------------------------------------------


def test_generation_recorder_noop_when_generation_is_none():
    _GenerationRecorder().record(output={"a": 1}, usage=_FakeUsage(1, 1))  # must not raise


# --- trace_url (Phase 9D's detail-view "View trace" link) -----------------


def test_trace_url_none_when_tracing_disabled(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PROJECT_ID", "proj-1")
    assert trace_url("t-abc") is None


def test_trace_url_none_when_project_id_unset(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.delenv("LANGFUSE_PROJECT_ID", raising=False)
    assert trace_url("t-abc") is None


def test_trace_url_builds_link_from_project_id_and_deterministic_trace_id(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PROJECT_ID", "proj-1")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    from langfuse import get_client

    expected_trace_id = get_client().create_trace_id(seed="t-abc")

    assert trace_url("t-abc") == f"https://cloud.langfuse.com/project/proj-1/traces/{expected_trace_id}"


def test_trace_url_respects_langfuse_host(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PROJECT_ID", "proj-1")
    monkeypatch.setenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com/")

    assert trace_url("t-abc").startswith("https://us.cloud.langfuse.com/project/proj-1/traces/")


def test_trace_url_same_thread_id_is_deterministic(monkeypatch):
    """Same reasoning as get_langchain_handler's trace_id derivation - the
    link must point at the same trace no matter how many times it's built."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PROJECT_ID", "proj-1")

    assert trace_url("t-xyz") == trace_url("t-xyz")
