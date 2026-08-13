"""CLI: score invoice_agent.extract.extract_invoice() against evals/dataset.jsonl.

Predictions are cached under evals/.cache/ (gitignored), keyed on
sha256(pdf_bytes + model + EXTRACTION_PROMPT), so a change to normalize.py
or scoring.py can be iterated on for free without re-paying for extraction.
extract_invoice runs at its default (non-zero) temperature - this measures
production behavior as configured, not a special deterministic eval mode -
so the cache also pins evals/report.md to one specific run.

If extraction raises for an item, the run continues (recorded as a full
miss - see evals/scoring.py's score_document) rather than aborting, so one
bad PDF can't take down the whole harness. Pass --fail-fast to abort
immediately instead, for debugging a specific failure.

Usage:
  python evals/run_eval.py
  python evals/run_eval.py --limit 2
  python evals/run_eval.py --model claude-haiku-4-5 --out evals/report_haiku.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_agent.extract import EXTRACTION_PROMPT, MODEL, extract_invoice  # noqa: E402

from evals.report import render_report, render_stdout_summary  # noqa: E402
from evals.scoring import aggregate, score_document  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "evals" / "dataset.jsonl"
DEFAULT_REPORT = REPO_ROOT / "evals" / "report.md"
CACHE_DIR = REPO_ROOT / "evals" / ".cache"


def _load_dataset(path: Path) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _cache_key(pdf_bytes: bytes, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(pdf_bytes)
    digest.update(model.encode("utf-8"))
    digest.update(EXTRACTION_PROMPT.encode("utf-8"))
    return digest.hexdigest()


def _predict(pdf_path: Path, model: str, use_cache: bool) -> tuple[dict | None, str | None]:
    """Returns (predicted_dict_or_None, error_message_or_None)."""
    pdf_bytes = pdf_path.read_bytes()
    cache_path = CACHE_DIR / f"{_cache_key(pdf_bytes, model)}.json"

    if use_cache and cache_path.exists():
        cached = json.loads(cache_path.read_text())
        return cached.get("predicted"), cached.get("error")

    try:
        invoice = extract_invoice(pdf_path, model=model)
        predicted, error = invoice.model_dump(mode="json"), None
    except Exception as exc:  # noqa: BLE001 - the harness must survive one bad PDF
        predicted, error = None, f"{type(exc).__name__}: {exc}"

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"predicted": predicted, "error": error}, indent=2))

    return predicted, error


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or None
    except Exception:  # noqa: BLE001 - the git SHA is a nice-to-have, not required
        return None


def run(
    dataset_path: Path,
    out_path: Path,
    limit: int | None,
    use_cache: bool,
    fail_fast: bool,
    model: str,
) -> int:
    rows = _load_dataset(dataset_path)
    if limit is not None:
        rows = rows[:limit]

    results = []
    for row in rows:
        pdf_path = REPO_ROOT / row["pdf_path"]
        print(f"  {row['id']}: {row['pdf_path']} ...", end=" ", flush=True)
        predicted, error = _predict(pdf_path, model, use_cache)

        if error and fail_fast:
            print("FAILED (--fail-fast)")
            raise RuntimeError(f"{row['id']} ({row['pdf_path']}): {error}")

        print("error" if error else "ok")
        results.append(
            score_document(
                row["id"],
                row.get("tags", {}),
                row["gold"],
                predicted,
                error=error,
                pdf_path=row["pdf_path"],
            )
        )

    agg = aggregate(results)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    report = render_report(
        agg,
        results,
        model=model,
        dataset_size=len(results),
        generated_at=generated_at,
        git_sha=_git_sha(),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    print()
    print(render_stdout_summary(agg, model))
    print()
    print(f"Full report written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score extract_invoice() against evals/dataset.jsonl."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=None, help="score only the first N items")
    parser.add_argument("--no-cache", action="store_true", help="ignore and overwrite the prediction cache")
    parser.add_argument("--fail-fast", action="store_true", help="abort on the first extraction error")
    parser.add_argument("--model", default=MODEL, help="override the extraction model")
    args = parser.parse_args()

    load_dotenv()

    return run(args.dataset, args.out, args.limit, not args.no_cache, args.fail_fast, args.model)


if __name__ == "__main__":
    sys.exit(main())
