# Extraction Eval Report

Generated: 2026-08-13T08:54:57Z · Model: `claude-sonnet-5` · Dataset: 20 invoices · `d076085`

## Headline

- **Extraction success rate:** 20/20 (100.0%)
- **Document-level exact match:** 20/20 (100.0%) — every scalar field AND every line item correct, the strictest number here
- **Field-level exact-match accuracy:** 100.0% — fraction of all (document, field) pairs extracted correctly
- **Micro-F1:** 1.000 (P=1.000, R=1.000)
- **Macro-F1:** 1.000 (unweighted mean across the 13 fields below — immune to line-item-count weighting)
- **Consistency-check pass rate (subtotal + tax == total):** 20/20 (100.0%)
- **Overall validation pass rate** (`validate_invoice`, all checks): 20/20 (100.0%)

## Per-field metrics

| Field | Exact | TP | FP | FN | Precision | Recall | F1 | Token-F1 |
|---|---|---|---|---|---|---|---|---|
| `invoice_number` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `invoice_date` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `vendor_name` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| `bill_to` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| `subtotal` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `tax` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `total` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `currency` | 100.0% | 20 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `due_date` | 100.0% | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `line_items.description` | 100.0% | 68 | 0 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| `line_items.quantity` | 100.0% | 68 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `line_items.unit_price` | 100.0% | 68 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |
| `line_items.amount` | 100.0% | 68 | 0 | 0 | 1.000 | 1.000 | 1.000 |  |

## Line items detail

Line-item count exact-match: 20/20 (100.0%)

No item-count mismatches.

## Slice breakdown

**By render type:**

| render | N | field exact-match | micro-F1 | doc exact-match |
|---|---|---|---|---|
| digital | 17 | 100.0% | 1.000 | 100.0% |
| scanned | 3 | 100.0% | 1.000 | 100.0% |

**By currency:**

| currency | N | field exact-match | micro-F1 |
|---|---|---|---|
| EUR | 3 | 100.0% | 1.000 |
| GBP | 2 | 100.0% | 1.000 |
| JPY | 1 | 100.0% | 1.000 |
| USD | 14 | 100.0% | 1.000 |

## Errors

No extraction errors.

## Notes

No unparseable dates or other normalization warnings.

## Per-document detail

| id | currency | render | field exact-match | consistency | flags |
|---|---|---|---|---|---|
| 01 | USD | digital | 21/21 | ✓ | — |
| 02 | USD | digital | 21/21 | ✓ | — |
| 03 | USD | digital | 21/21 | ✓ | — |
| 04 | EUR | digital | 17/17 | ✓ | — |
| 05 | GBP | digital | 25/25 | ✓ | — |
| 06 | USD | digital | 13/13 | ✓ | — |
| 07 | USD | digital | 33/33 | ✓ | — |
| 08 | USD | digital | 29/29 | ✓ | — |
| 09 | USD | digital | 17/17 | ✓ | — |
| 10 | EUR | digital | 21/21 | ✓ | — |
| 11 | USD | scanned | 25/25 | ✓ | — |
| 12 | EUR | scanned | 17/17 | ✓ | — |
| 13 | USD | scanned | 29/29 | ✓ | — |
| 14 | USD | digital | 21/21 | ✓ | — |
| 15 | USD | digital | 25/25 | ✓ | — |
| 16 | USD | digital | 21/21 | ✓ | — |
| 17 | GBP | digital | 17/17 | ✓ | — |
| 18 | USD | digital | 13/13 | ✓ | — |
| 19 | USD | digital | 45/45 | ✓ | — |
| 20 | JPY | digital | 21/21 | ✓ | — |

## Methodology
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
