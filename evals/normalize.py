"""Normalization for eval comparisons - pure functions, stdlib only.

Predicted and gold values must both be normalized before comparison (see
evals/scoring.py). Deliberately does NOT use python-dateutil: it's not a
pinned dependency (only present transitively via pandas), and a scorer
that silently guesses date/number ordering would corrupt the metric it
publishes. Unparseable values normalize to None, which the scorer counts
as a miss rather than a guessed match - see evals/report.py's Notes
section for where that's surfaced.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

MONEY_SYMBOLS = "$€£¥₹"

CURRENCY_SYMBOL_MAP = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
}

# Deliberately excludes slash-dates ("%m/%d/%Y", "%d/%m/%Y") - irreducibly
# ambiguous without a locale, so left unparsed rather than guessed.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d-%b-%Y",
)


def normalize_string(value) -> str | None:
    """NFKC-normalize, strip, collapse internal whitespace, casefold, and
    drop trailing punctuation. Deliberately does NOT strip corporate
    suffixes (Inc/LLC/Ltd) - too aggressive, would hide genuine errors."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.casefold()
    text = text.rstrip(".,;:")
    return text


def normalize_money(value) -> float | None:
    """Strip currency symbols/commas/spaces, resolve decimal-vs-grouping
    separator ambiguity, cast to a 2-decimal float. Returns None if the
    value can't be confidently parsed as a number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    text = str(value).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    for symbol in MONEY_SYMBOLS:
        text = text.replace(symbol, "")
    text = text.replace(" ", "").replace(" ", "")
    text = text.replace("−", "-")  # unicode minus sign -> ascii

    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    if not text:
        return None

    has_dot = "." in text
    has_comma = "," in text

    if has_dot and has_comma:
        # Whichever separator appears last is the decimal point.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        # Only commas: a comma not followed by exactly 3 digits is a
        # decimal separator ("12,5"); otherwise treat as grouping ("12,500").
        tail = text.split(",")[-1]
        text = text.replace(",", ".") if len(tail) != 3 else text.replace(",", "")
    # else: only dots (or none) - float() handles it as-is.

    try:
        result = float(text)
    except ValueError:
        return None

    if negative:
        result = -result
    return round(result, 2)


def normalize_date(value) -> str | None:
    """Parse to an ISO 8601 'YYYY-MM-DD' string, or None if unparseable."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_currency(value) -> str | None:
    """Map a currency symbol to its ISO 4217 code, or uppercase an
    already-coded value."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in CURRENCY_SYMBOL_MAP:
        return CURRENCY_SYMBOL_MAP[text]
    return text.upper()


def values_equal(a, b, kind: str) -> bool:
    """Compare two already-normalized values. Both must be non-None to
    count as equal - callers decide separately how to treat None/None or
    None/value pairs (see evals/scoring.py's due_date handling)."""
    if kind not in ("money", "date", "string"):
        raise ValueError(f"unknown kind: {kind!r}")
    if a is None or b is None:
        return False
    if kind == "money":
        return abs(a - b) <= 0.005
    return a == b
