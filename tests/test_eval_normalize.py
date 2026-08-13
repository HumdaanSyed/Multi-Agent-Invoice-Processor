"""Tests for evals.normalize - pure functions, no API calls."""

from evals.normalize import (
    normalize_currency,
    normalize_date,
    normalize_money,
    normalize_string,
    values_equal,
)


# --- normalize_string --------------------------------------------------


def test_normalize_string_none():
    assert normalize_string(None) is None


def test_normalize_string_strips_and_casefolds():
    assert normalize_string("  Acme Co.  ") == "acme co"


def test_normalize_string_collapses_internal_whitespace():
    assert normalize_string("Acme   Cloud\tHosting") == "acme cloud hosting"


def test_normalize_string_drops_trailing_punctuation():
    assert normalize_string("Springfield, IL,") == "springfield, il"


def test_normalize_string_does_not_strip_llc_suffix():
    assert normalize_string("Acme Cloud Hosting LLC") == "acme cloud hosting llc"


# --- normalize_money -----------------------------------------------------


def test_normalize_money_none():
    assert normalize_money(None) is None


def test_normalize_money_numeric_passthrough():
    assert normalize_money(657.723) == 657.72


def test_normalize_money_strips_dollar_and_commas():
    assert normalize_money("$1,234.56") == 1234.56


def test_normalize_money_euro_symbol():
    assert normalize_money("€99.00") == 99.00


def test_normalize_money_thousands_comma_no_decimal():
    assert normalize_money("12,500") == 12500.0


def test_normalize_money_decimal_comma():
    assert normalize_money("12,5") == 12.5


def test_normalize_money_both_separators_euro_style():
    # European style: "." thousands, "," decimal
    assert normalize_money("1.234,56") == 1234.56


def test_normalize_money_both_separators_us_style():
    assert normalize_money("1,234.56") == 1234.56


def test_normalize_money_parens_negative():
    assert normalize_money("(50.00)") == -50.00


def test_normalize_money_unparseable_returns_none():
    assert normalize_money("not a number") is None


def test_normalize_money_empty_string_returns_none():
    assert normalize_money("") is None


# --- normalize_date --------------------------------------------------------


def test_normalize_date_none():
    assert normalize_date(None) is None


def test_normalize_date_already_iso():
    assert normalize_date("2026-07-01") == "2026-07-01"


def test_normalize_date_long_form():
    assert normalize_date("15 June 2026") == "2026-06-15"


def test_normalize_date_month_day_comma_year():
    assert normalize_date("June 15, 2026") == "2026-06-15"


def test_normalize_date_unparseable_returns_none():
    assert normalize_date("whenever") is None


def test_normalize_date_ambiguous_slash_date_returns_none():
    # Deliberately not parsed - ambiguous without a locale.
    assert normalize_date("03/04/2026") is None


# --- normalize_currency --------------------------------------------------


def test_normalize_currency_symbol():
    assert normalize_currency("$") == "USD"
    assert normalize_currency("€") == "EUR"
    assert normalize_currency("£") == "GBP"


def test_normalize_currency_code_uppercased():
    assert normalize_currency("usd") == "USD"


def test_normalize_currency_none():
    assert normalize_currency(None) is None


# --- values_equal ------------------------------------------------------------


def test_values_equal_money_within_tolerance():
    assert values_equal(10.001, 10.002, "money") is True


def test_values_equal_money_outside_tolerance():
    assert values_equal(10.00, 10.01, "money") is False


def test_values_equal_date_exact():
    assert values_equal("2026-07-01", "2026-07-01", "date") is True


def test_values_equal_date_different():
    assert values_equal("2026-07-01", "2026-07-02", "date") is False


def test_values_equal_string_exact():
    assert values_equal("acme co", "acme co", "string") is True


def test_values_equal_none_never_equal():
    assert values_equal(None, None, "money") is False
    assert values_equal(None, 10.0, "money") is False


def test_values_equal_unknown_kind_raises():
    try:
        values_equal("a", "a", "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass
