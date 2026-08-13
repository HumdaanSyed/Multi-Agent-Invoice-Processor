"""Field-level scoring: TP/FP/FN counting, line-item matching, aggregation.

Pure - operates on plain dicts (predicted = extract_invoice(...).model_dump(),
gold = the JSONL row's "gold" object), never touches the network or a live
Invoice object. `invoice_agent.validate.validate_invoice` accepts dict or
Invoice, so it's called directly on the predicted dict.

Methodology, in brief (full version in evals/report.py's rendered output):
  - 8 required scalar fields: normalized-equal -> TP, else -> FP+FN both.
    This makes P == R == F1 == exact-match for these fields BY CONSTRUCTION,
    not a bug.
  - due_date (the only optional field): both-None is a true negative,
    excluded from P/R but counted as an exact match (correctly abstaining
    is correct). One-None-one-value is FP or FN.
  - line_items: greedy best-match (not positional - one spurious/missing
    row would otherwise misattribute errors to every later item), then
    scored per sub-field like the scalars.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from invoice_agent.validate import TOLERANCE, validate_invoice

from evals.normalize import (
    normalize_currency,
    normalize_date,
    normalize_money,
    normalize_string,
    values_equal,
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    normalize: Callable
    kind: str  # "money" | "date" | "string"
    token_f1: bool = False  # also compute a supplementary token-overlap F1


SCALAR_FIELDS = [
    FieldSpec("invoice_number", normalize_string, "string"),
    FieldSpec("invoice_date", normalize_date, "date"),
    FieldSpec("vendor_name", normalize_string, "string", token_f1=True),
    FieldSpec("bill_to", normalize_string, "string", token_f1=True),
    FieldSpec("subtotal", normalize_money, "money"),
    FieldSpec("tax", normalize_money, "money"),
    FieldSpec("total", normalize_money, "money"),
    FieldSpec("currency", normalize_currency, "string"),
]

LINE_ITEM_FIELDS = [
    FieldSpec("description", normalize_string, "string", token_f1=True),
    FieldSpec("quantity", normalize_money, "money"),
    FieldSpec("unit_price", normalize_money, "money"),
    FieldSpec("amount", normalize_money, "money"),
]

FIELD_ROWS = (
    [f.name for f in SCALAR_FIELDS]
    + ["due_date"]
    + [f"line_items.{f.name}" for f in LINE_ITEM_FIELDS]
)


def totals_consistent(subtotal, tax, total) -> bool:
    """The roadmap's required consistency check: subtotal + tax == total.

    Imports TOLERANCE from invoice_agent.validate (no duplicated constant)
    rather than string-matching validate_invoice()'s flag text, so a
    wording change there can't silently break this into always-passing.
    """
    if subtotal is None or tax is None or total is None:
        return False
    return abs(round(subtotal + tax, 2) - total) <= TOLERANCE


def token_f1(pred: str | None, gold: str | None) -> float | None:
    """Supplementary bag-of-words F1 for free-text fields - not the
    headline metric, but distinguishes "totally wrong" from "close but
    incomplete" (e.g. dropped the street address from bill_to)."""
    if pred is None or gold is None:
        return None
    pred_tokens = pred.split()
    gold_tokens = gold.split()
    if not pred_tokens or not gold_tokens:
        return 1.0 if pred_tokens == gold_tokens else 0.0
    overlap = sum((Counter(pred_tokens) & Counter(gold_tokens)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def _item_similarity(pred_item: dict, gold_item: dict) -> float:
    desc_sim = SequenceMatcher(
        None,
        normalize_string(pred_item.get("description")) or "",
        normalize_string(gold_item.get("description")) or "",
    ).ratio()
    money_hits = []
    for field_name in ("quantity", "unit_price", "amount"):
        p = normalize_money(pred_item.get(field_name))
        g = normalize_money(gold_item.get(field_name))
        money_hits.append(1.0 if values_equal(p, g, "money") else 0.0)
    money_sim = sum(money_hits) / len(money_hits)
    return 0.5 * desc_sim + 0.5 * money_sim


def match_line_items(
    pred_items: list[dict], gold_items: list[dict], threshold: float = 0.5
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy best-match pairing between predicted and gold line items.

    Returns (matches, unmatched_pred_indices, unmatched_gold_indices).
    Positional matching is deliberately not used: one spurious or missing
    row would otherwise shift every subsequent item and misattribute N
    errors to a single mistake.
    """
    candidates = []
    for i, p in enumerate(pred_items):
        for j, g in enumerate(gold_items):
            sim = _item_similarity(p, g)
            if sim >= threshold:
                candidates.append((sim, i, j))
    # Highest similarity first; (i, j) tie-break for full determinism -
    # required, since evals/report.md is committed and must be reproducible.
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))

    matched_pred: set[int] = set()
    matched_gold: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _sim, i, j in candidates:
        if i in matched_pred or j in matched_gold:
            continue
        matched_pred.add(i)
        matched_gold.add(j)
        matches.append((i, j))

    unmatched_pred = [i for i in range(len(pred_items)) if i not in matched_pred]
    unmatched_gold = [j for j in range(len(gold_items)) if j not in matched_gold]
    return matches, unmatched_pred, unmatched_gold


@dataclass
class FieldOutcome:
    field: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    exact: bool = False


def _score_scalar_field(
    spec: FieldSpec, gold_value, pred_value, extraction_failed: bool
) -> FieldOutcome:
    if extraction_failed:
        return FieldOutcome(spec.name, fn=1, exact=False)
    gold_norm = spec.normalize(gold_value)
    pred_norm = spec.normalize(pred_value)
    if values_equal(pred_norm, gold_norm, spec.kind):
        return FieldOutcome(spec.name, tp=1, exact=True)
    return FieldOutcome(spec.name, fp=1, fn=1, exact=False)


def _score_due_date(gold_value, pred_value, extraction_failed: bool) -> FieldOutcome:
    gold_norm = normalize_date(gold_value)
    if extraction_failed:
        if gold_norm is None:
            return FieldOutcome("due_date", tn=1, exact=True)
        return FieldOutcome("due_date", fn=1, exact=False)

    pred_norm = normalize_date(pred_value)
    if gold_norm is None and pred_norm is None:
        return FieldOutcome("due_date", tn=1, exact=True)
    if gold_norm is None and pred_norm is not None:
        return FieldOutcome("due_date", fp=1, exact=False)
    if gold_norm is not None and pred_norm is None:
        return FieldOutcome("due_date", fn=1, exact=False)
    if values_equal(pred_norm, gold_norm, "date"):
        return FieldOutcome("due_date", tp=1, exact=True)
    return FieldOutcome("due_date", fp=1, fn=1, exact=False)


def _score_line_items(
    gold_items: list[dict], pred_items: list[dict], extraction_failed: bool
) -> tuple[list[FieldOutcome], bool, list[float]]:
    outcomes: list[FieldOutcome] = []
    desc_token_f1s: list[float] = []

    if extraction_failed:
        matches, unmatched_pred, unmatched_gold = [], [], list(range(len(gold_items)))
    else:
        matches, unmatched_pred, unmatched_gold = match_line_items(pred_items, gold_items)

    for pred_idx, gold_idx in matches:
        p_item = pred_items[pred_idx]
        g_item = gold_items[gold_idx]
        for spec in LINE_ITEM_FIELDS:
            gold_norm = spec.normalize(g_item.get(spec.name))
            pred_norm = spec.normalize(p_item.get(spec.name))
            if values_equal(pred_norm, gold_norm, spec.kind):
                outcomes.append(FieldOutcome(f"line_items.{spec.name}", tp=1, exact=True))
            else:
                outcomes.append(FieldOutcome(f"line_items.{spec.name}", fp=1, fn=1, exact=False))
        desc_f1 = token_f1(
            normalize_string(p_item.get("description")), normalize_string(g_item.get("description"))
        )
        if desc_f1 is not None:
            desc_token_f1s.append(desc_f1)

    for _ in unmatched_pred:
        for spec in LINE_ITEM_FIELDS:
            outcomes.append(FieldOutcome(f"line_items.{spec.name}", fp=1, exact=False))
    for _ in unmatched_gold:
        for spec in LINE_ITEM_FIELDS:
            outcomes.append(FieldOutcome(f"line_items.{spec.name}", fn=1, exact=False))

    count_match = len(pred_items) == len(gold_items)
    return outcomes, count_match, desc_token_f1s


@dataclass
class DocumentResult:
    doc_id: str
    tags: dict
    error: str | None
    gold: dict
    predicted: dict | None
    field_outcomes: list[FieldOutcome]
    token_f1_scores: dict[str, float | None]
    line_item_count_match: bool
    consistency_pass: bool
    validation_passed: bool
    validation_flags: list[str]
    document_exact_match: bool
    pdf_path: str = ""


def score_document(
    doc_id: str,
    tags: dict,
    gold: dict,
    predicted: dict | None,
    error: str | None = None,
    pdf_path: str = "",
) -> DocumentResult:
    """Score one document. `predicted` is `extract_invoice(...).model_dump()`
    (already-normalized-type dict) or None if extraction raised - in which
    case `error` should hold the exception message."""
    extraction_failed = predicted is None or error is not None

    outcomes: list[FieldOutcome] = []
    token_f1_scores: dict[str, float | None] = {}

    for spec in SCALAR_FIELDS:
        gold_value = gold.get(spec.name)
        pred_value = None if extraction_failed else predicted.get(spec.name)
        outcomes.append(_score_scalar_field(spec, gold_value, pred_value, extraction_failed))
        if spec.token_f1:
            gv = normalize_string(gold_value)
            pv = None if extraction_failed else normalize_string(pred_value)
            token_f1_scores[spec.name] = token_f1(pv, gv)

    outcomes.append(
        _score_due_date(
            gold.get("due_date"),
            None if extraction_failed else predicted.get("due_date"),
            extraction_failed,
        )
    )

    gold_items = gold.get("line_items") or []
    pred_items = [] if extraction_failed else (predicted.get("line_items") or [])
    line_outcomes, count_match, desc_token_f1s = _score_line_items(
        gold_items, pred_items, extraction_failed
    )
    outcomes.extend(line_outcomes)
    token_f1_scores["line_items.description"] = (
        sum(desc_token_f1s) / len(desc_token_f1s) if desc_token_f1s else None
    )

    if extraction_failed:
        consistency_pass = False
        validation_passed = False
        validation_flags = [f"extraction failed: {error}" if error else "extraction failed"]
    else:
        consistency_pass = totals_consistent(
            predicted.get("subtotal"), predicted.get("tax"), predicted.get("total")
        )
        validation_result = validate_invoice(predicted, duplicate_checker=None)
        validation_passed = validation_result.passed
        validation_flags = validation_result.flags

    return DocumentResult(
        doc_id=doc_id,
        tags=tags,
        error=error,
        gold=gold,
        predicted=predicted,
        pdf_path=pdf_path,
        field_outcomes=outcomes,
        token_f1_scores=token_f1_scores,
        line_item_count_match=count_match,
        consistency_pass=consistency_pass,
        validation_passed=validation_passed,
        validation_flags=validation_flags,
        document_exact_match=all(o.exact for o in outcomes),
    )


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    exact_matches: int = 0
    total: int = 0

    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    def f1(self) -> float | None:
        p, r = self.precision(), self.recall()
        if p is None or r is None or (p + r) == 0:
            return None
        return 2 * p * r / (p + r)

    def exact_match_rate(self) -> float | None:
        return self.exact_matches / self.total if self.total else None


@dataclass
class EvalResult:
    field_counts: dict[str, Counts]
    token_f1_avg: dict[str, float | None]
    micro_precision: float | None
    micro_recall: float | None
    micro_f1: float | None
    macro_f1: float | None
    field_exact_match_rate: float | None
    document_exact_match_rate: float | None
    extraction_success_rate: float | None
    consistency_pass_rate: float | None
    validation_pass_rate: float | None
    line_item_count_match_rate: float | None
    n_documents: int
    # Explicit counts alongside the rates above, so "N/M" headline display
    # doesn't have to reconstruct M from a rounded rate.
    document_exact_match_count: int = 0
    extraction_success_count: int = 0
    consistency_pass_count: int = 0
    validation_pass_count: int = 0
    line_item_count_match_count: int = 0


def aggregate(results: list[DocumentResult]) -> EvalResult:
    """Roll up a list of DocumentResults. Callers wanting a slice breakdown
    (e.g. digital vs scanned) just filter `results` by `.tags` and call
    this again - no grouping logic is baked in here."""
    field_counts: dict[str, Counts] = {name: Counts() for name in FIELD_ROWS}
    token_f1_sums: dict[str, list[float]] = {}

    doc_exact = 0
    extraction_ok = 0
    consistency_ok = 0
    validation_ok = 0
    count_match_ok = 0

    for doc in results:
        for outcome in doc.field_outcomes:
            counts = field_counts[outcome.field]
            counts.tp += outcome.tp
            counts.fp += outcome.fp
            counts.fn += outcome.fn
            counts.tn += outcome.tn
            counts.total += 1
            if outcome.exact:
                counts.exact_matches += 1
        for field_name, score in doc.token_f1_scores.items():
            if score is not None:
                token_f1_sums.setdefault(field_name, []).append(score)
        if doc.document_exact_match:
            doc_exact += 1
        if doc.error is None:
            extraction_ok += 1
        if doc.consistency_pass:
            consistency_ok += 1
        if doc.validation_passed:
            validation_ok += 1
        if doc.line_item_count_match:
            count_match_ok += 1

    n = len(results)

    total_tp = sum(c.tp for c in field_counts.values())
    total_fp = sum(c.fp for c in field_counts.values())
    total_fn = sum(c.fn for c in field_counts.values())
    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else None
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else None
    micro_f1 = None
    if micro_p is not None and micro_r is not None and (micro_p + micro_r) > 0:
        micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r)

    field_f1s = [f1 for c in field_counts.values() if (f1 := c.f1()) is not None]
    macro_f1 = sum(field_f1s) / len(field_f1s) if field_f1s else None

    total_exact = sum(c.exact_matches for c in field_counts.values())
    total_instances = sum(c.total for c in field_counts.values())
    field_exact_rate = total_exact / total_instances if total_instances else None

    token_f1_avg = {name: sum(v) / len(v) for name, v in token_f1_sums.items()}

    return EvalResult(
        field_counts=field_counts,
        token_f1_avg=token_f1_avg,
        micro_precision=micro_p,
        micro_recall=micro_r,
        micro_f1=micro_f1,
        macro_f1=macro_f1,
        field_exact_match_rate=field_exact_rate,
        document_exact_match_rate=(doc_exact / n if n else None),
        extraction_success_rate=(extraction_ok / n if n else None),
        consistency_pass_rate=(consistency_ok / n if n else None),
        validation_pass_rate=(validation_ok / n if n else None),
        line_item_count_match_rate=(count_match_ok / n if n else None),
        n_documents=n,
        document_exact_match_count=doc_exact,
        extraction_success_count=extraction_ok,
        consistency_pass_count=consistency_ok,
        validation_pass_count=validation_ok,
        line_item_count_match_count=count_match_ok,
    )
