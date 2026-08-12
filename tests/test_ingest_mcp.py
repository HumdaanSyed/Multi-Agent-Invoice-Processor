"""Tests for invoice_agent.ingest_mcp's pure helper functions - no MCP
server, no live network needed."""

from invoice_agent.ingest_mcp import (
    _extract_scalar_result,
    _extract_tool_result,
    _is_duplicate_only,
)


# --- _extract_tool_result ---------------------------------------------------


def test_extract_tool_result_parses_json_dict_blocks():
    raw = [{"type": "text", "text": '{"id": "1", "name": "a"}'}]
    assert _extract_tool_result(raw) == [{"id": "1", "name": "a"}]


def test_extract_tool_result_falls_back_to_raw_text_for_non_json_string():
    raw = [{"type": "text", "text": "/tmp/attachments/invoice.pdf"}]
    assert _extract_tool_result(raw) == ["/tmp/attachments/invoice.pdf"]


def test_extract_tool_result_parses_json_bool():
    raw = [{"type": "text", "text": "true"}]
    assert _extract_tool_result(raw) == [True]


def test_extract_tool_result_multiple_blocks():
    raw = [
        {"type": "text", "text": "/a/one.pdf"},
        {"type": "text", "text": "/a/two.pdf"},
    ]
    assert _extract_tool_result(raw) == ["/a/one.pdf", "/a/two.pdf"]


def test_extract_tool_result_empty_list():
    assert _extract_tool_result([]) == []


def test_extract_tool_result_non_list_passthrough():
    assert _extract_tool_result("already a value") == "already a value"


# --- _extract_scalar_result --------------------------------------------------


def test_extract_scalar_result_unwraps_true():
    raw = [{"type": "text", "text": "true"}]
    assert _extract_scalar_result(raw) is True


def test_extract_scalar_result_unwraps_false_not_truthy_list():
    raw = [{"type": "text", "text": "false"}]
    result = _extract_scalar_result(raw)
    assert result is False
    assert not result  # guards against the [False]-is-truthy footgun


def test_extract_scalar_result_empty_returns_none():
    assert _extract_scalar_result([]) is None


# --- _is_duplicate_only ------------------------------------------------------


def test_is_duplicate_only_true_for_single_duplicate_flag():
    assert _is_duplicate_only(["Possible duplicate: vendor='Acme' number='1'"]) is True


def test_is_duplicate_only_false_when_other_flags_present():
    flags = [
        "Possible duplicate: vendor='Acme' number='1'",
        "Subtotal + tax = 10.00 but total is 999.99",
    ]
    assert _is_duplicate_only(flags) is False


def test_is_duplicate_only_false_for_non_duplicate_flags():
    assert _is_duplicate_only(["Subtotal + tax = 10.00 but total is 999.99"]) is False


def test_is_duplicate_only_false_for_empty_flags():
    assert _is_duplicate_only([]) is False
