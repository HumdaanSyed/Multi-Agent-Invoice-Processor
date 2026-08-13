"""Renders scored eval results into markdown.

Pure - takes already-computed EvalResult/DocumentResult objects, returns a
string. evals/run_eval.py calls this once and both prints a slice of the
result to stdout and writes the full result to evals/report.md, so the two
outputs can never disagree with each other.
"""

from __future__ import annotations

from evals.normalize import normalize_date
from evals.scoring import SCALAR_FIELDS, DocumentResult, EvalResult, aggregate

TOKEN_F1_FIELDS = [f.name for f in SCALAR_FIELDS if f.token_f1] + ["line_items.description"]


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _score(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def render_field_table(agg: EvalResult) -> str:
    header = "| Field | Exact | TP | FP | FN | Precision | Recall | F1 | Token-F1 |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    rows = [header, sep]
    for name, counts in agg.field_counts.items():
        token_f1_col = _score(agg.token_f1_avg.get(name)) if name in TOKEN_F1_FIELDS else ""
        rows.append(
            f"| `{name}` | {_pct(counts.exact_match_rate())} | {counts.tp} | {counts.fp} | "
            f"{counts.fn} | {_score(counts.precision())} | {_score(counts.recall())} | "
            f"{_score(counts.f1())} | {token_f1_col} |"
        )
    return "\n".join(rows)


def render_headline(agg: EvalResult) -> str:
    lines = [
        f"- **Extraction success rate:** {agg.extraction_success_count}/{agg.n_documents} "
        f"({_pct(agg.extraction_success_rate)})",
        f"- **Document-level exact match:** {agg.document_exact_match_count}/{agg.n_documents} "
        f"({_pct(agg.document_exact_match_rate)}) — every scalar field AND every line item "
        "correct, the strictest number here",
        f"- **Field-level exact-match accuracy:** {_pct(agg.field_exact_match_rate)} — "
        "fraction of all (document, field) pairs extracted correctly",
        f"- **Micro-F1:** {_score(agg.micro_f1)} "
        f"(P={_score(agg.micro_precision)}, R={_score(agg.micro_recall)})",
        f"- **Macro-F1:** {_score(agg.macro_f1)} (unweighted mean across the "
        f"{len(agg.field_counts)} fields below — immune to line-item-count weighting)",
        f"- **Consistency-check pass rate (subtotal + tax == total):** "
        f"{agg.consistency_pass_count}/{agg.n_documents} ({_pct(agg.consistency_pass_rate)})",
        f"- **Overall validation pass rate** (`validate_invoice`, all checks): "
        f"{agg.validation_pass_count}/{agg.n_documents} ({_pct(agg.validation_pass_rate)})",
    ]
    return "\n".join(lines)


def render_line_items_detail(results: list[DocumentResult], agg: EvalResult) -> str:
    lines = [
        f"Line-item count exact-match: {agg.line_item_count_match_count}/{agg.n_documents} "
        f"({_pct(agg.line_item_count_match_rate)})",
        "",
    ]
    mismatches = [
        r
        for r in results
        if not r.line_item_count_match and r.predicted is not None
    ]
    if mismatches:
        lines.append("Documents where predicted/gold item counts differ:")
        lines.append("")
        lines.append("| id | predicted items | gold items |")
        lines.append("|---|---|---|")
        for r in mismatches:
            pred_count = len(r.predicted.get("line_items") or [])
            gold_count = len(r.gold.get("line_items") or [])
            lines.append(f"| {r.doc_id} | {pred_count} | {gold_count} |")
    else:
        lines.append("No item-count mismatches.")
    return "\n".join(lines)


def render_slice_breakdown(results: list[DocumentResult]) -> str:
    sections = []

    render_tags = sorted({r.tags.get("render") for r in results if r.tags.get("render")})
    if len(render_tags) > 1:
        rows = ["| render | N | field exact-match | micro-F1 | doc exact-match |", "|---|---|---|---|---|"]
        for tag in render_tags:
            subset = [r for r in results if r.tags.get("render") == tag]
            sub_agg = aggregate(subset)
            rows.append(
                f"| {tag} | {sub_agg.n_documents} | {_pct(sub_agg.field_exact_match_rate)} | "
                f"{_score(sub_agg.micro_f1)} | {_pct(sub_agg.document_exact_match_rate)} |"
            )
        sections.append("**By render type:**\n\n" + "\n".join(rows))

    currency_tags = sorted({r.tags.get("currency") for r in results if r.tags.get("currency")})
    if len(currency_tags) > 1:
        rows = ["| currency | N | field exact-match | micro-F1 |", "|---|---|---|---|"]
        for tag in currency_tags:
            subset = [r for r in results if r.tags.get("currency") == tag]
            sub_agg = aggregate(subset)
            rows.append(
                f"| {tag} | {sub_agg.n_documents} | {_pct(sub_agg.field_exact_match_rate)} | "
                f"{_score(sub_agg.micro_f1)} |"
            )
        sections.append("**By currency:**\n\n" + "\n".join(rows))

    return "\n\n".join(sections) if sections else "(not enough tag variety to break down)"


def render_errors(results: list[DocumentResult]) -> str:
    errors = [r for r in results if r.error is not None]
    if not errors:
        return "No extraction errors."
    lines = ["| id | pdf_path | error |", "|---|---|---|"]
    for r in errors:
        message = r.error.splitlines()[0] if r.error else ""
        lines.append(f"| {r.doc_id} | `{r.pdf_path}` | {message} |")
    return "\n".join(lines)


def render_notes(results: list[DocumentResult]) -> str:
    notes: list[str] = []
    for r in results:
        if r.predicted is None:
            continue
        for spec in SCALAR_FIELDS:
            if spec.kind != "date":
                continue
            if normalize_date(r.predicted.get(spec.name)) is None:
                notes.append(
                    f"- `{r.doc_id}`: predicted `{spec.name}` = "
                    f"{r.predicted.get(spec.name)!r} did not parse as a date"
                )
        # due_date isn't in SCALAR_FIELDS (it's optional, scored separately -
        # see _score_due_date), but an unparseable-yet-present prediction is
        # exactly the case this section exists to surface. A genuinely
        # absent due_date is a correct abstention, not a warning.
        raw_due = r.predicted.get("due_date")
        if raw_due and normalize_date(raw_due) is None:
            notes.append(f"- `{r.doc_id}`: predicted `due_date` = {raw_due!r} did not parse as a date")
    if not notes:
        return "No unparseable dates or other normalization warnings."
    return "\n".join(notes)


def render_document_appendix(results: list[DocumentResult]) -> str:
    lines = [
        "| id | currency | render | field exact-match | consistency | flags |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        total_fields = len(r.field_outcomes)
        exact_fields = sum(1 for o in r.field_outcomes if o.exact)
        flags = "; ".join(r.validation_flags) if r.validation_flags else "—"
        status = "ERROR" if r.error else ("✓" if r.consistency_pass else "✗")
        lines.append(
            f"| {r.doc_id} | {r.tags.get('currency', '?')} | {r.tags.get('render', '?')} | "
            f"{exact_fields}/{total_fields} | {status} | {flags} |"
        )
    return "\n".join(lines)


METHODOLOGY = """
- **Normalization** happens before every comparison: strings are
  NFKC-normalized, whitespace-collapsed, and casefolded; money values have
  currency symbols/commas stripped and are compared to the cent (±0.005);
  dates are parsed to ISO 8601 or left as `None` (never guessed) if
  ambiguous. See `evals/normalize.py`.
- **P == R == F1 == exact-match for the 8 required scalar fields, by
  construction** - not a bug. Every document has exactly one gold value
  and one predicted value for these, so a mismatch is simultaneously one
  wrong assertion (FP) and one missed correct assertion (FN).
- **`due_date`** is the only optional field. Both gold and predicted being
  absent is a true negative (correct abstention) - excluded from
  precision/recall but counted as an exact match.
- **`line_items`** are matched greedily (highest description/amount
  similarity first, not by position), so one spurious or missing row
  doesn't cascade into misattributed errors on every later item. Extra
  predicted items are false positives; missed gold items are false
  negatives - this is the one place precision and recall genuinely
  diverge in this report.
- **Micro-F1** sums TP/FP/FN across every field instance; documents with
  more line items contribute proportionally more instances. **Macro-F1**
  is the unweighted mean of the per-field F1s and is immune to that
  weighting.
- **This eval set is synthetic** (`scripts/generate_eval_set.py`), not
  hand-labeled real invoices. Gold labels are exact by construction (the
  same arithmetic that draws each PDF produces its label), which removes
  hand-transcription error from the ground truth - but synthetic invoices
  share one renderer, so real-world layout diversity is wider than what's
  tested here. Treat these numbers as an upper bound on real-world
  extraction accuracy, not a direct estimate of it.
"""


def render_report(
    agg: EvalResult,
    results: list[DocumentResult],
    *,
    model: str,
    dataset_size: int,
    generated_at: str,
    git_sha: str | None = None,
) -> str:
    sha_line = f" · `{git_sha}`" if git_sha else ""
    parts = [
        "# Extraction Eval Report",
        "",
        f"Generated: {generated_at} · Model: `{model}` · Dataset: {dataset_size} invoices{sha_line}",
        "",
        "## Headline",
        "",
        render_headline(agg),
        "",
        "## Per-field metrics",
        "",
        render_field_table(agg),
        "",
        "## Line items detail",
        "",
        render_line_items_detail(results, agg),
        "",
        "## Slice breakdown",
        "",
        render_slice_breakdown(results),
        "",
        "## Errors",
        "",
        render_errors(results),
        "",
        "## Notes",
        "",
        render_notes(results),
        "",
        "## Per-document detail",
        "",
        render_document_appendix(results),
        "",
        "## Methodology",
        METHODOLOGY.strip(),
        "",
    ]
    return "\n".join(parts)


def render_stdout_summary(agg: EvalResult, model: str) -> str:
    """The subset printed to the terminal - headline + field table only."""
    return "\n".join(
        [
            "=== Extraction Eval ===",
            f"Model: {model}",
            "",
            render_headline(agg),
            "",
            render_field_table(agg),
        ]
    )
