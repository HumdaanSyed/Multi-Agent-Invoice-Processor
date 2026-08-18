"""Config-only readiness checks for GET /health/ready.

Reimplements the placeholder-detection logic from scripts/smoke_test.py,
but structured (one `ReadinessCheck` per service) and silent (no prints),
and without smoke_test.py's live network calls by default. Deliberately NOT
importing/reusing smoke_test.py directly - that script always makes live
API calls, while /health/ready wants zero network by default: a container
healthcheck loop (Phase 10's Docker HEALTHCHECK) polls every ~30s, and a
route that calls the Anthropic API on every poll risks a transient upstream
blip getting the orchestrator to kill and restart a healthy container
mid-invoice-run. Live checks are opt-in via `deep=True` for manual
debugging, never for the healthcheck itself - see GET /health (liveness,
zero I/O) vs GET /health/ready (this module) in app/routes.py.

The ~30 lines of duplicated placeholder-detection logic vs. smoke_test.py
is deliberate, not an oversight - see REVIEW.md.
"""

from __future__ import annotations

import os

from app.models import ReadinessCheck, ReadinessResponse
from app.service import GraphService

_REQUIRED_CHECKS = ("anthropic", "supabase", "checkpointer")


def _check_anthropic_config() -> ReadinessCheck:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key.startswith("sk-ant-your-key"):
        return ReadinessCheck(configured=False, detail="ANTHROPIC_API_KEY is missing or still a placeholder.")
    return ReadinessCheck(configured=True)


def _check_supabase_config() -> ReadinessCheck:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or "your-project-ref" in url or not key or key.startswith("your-supabase"):
        return ReadinessCheck(
            configured=False, detail="SUPABASE_URL / SUPABASE_KEY missing or still placeholders."
        )
    return ReadinessCheck(configured=True)


def _check_langfuse_config() -> ReadinessCheck:
    from invoice_agent.tracing import tracing_enabled

    if not tracing_enabled():
        return ReadinessCheck(
            configured=False,
            detail="Tracing disabled (LANGFUSE_PUBLIC_KEY/SECRET_KEY not set) - optional, "
            "the pipeline runs untraced.",
        )
    return ReadinessCheck(configured=True)


def _check_checkpointer(service: GraphService) -> ReadinessCheck:
    try:
        service.get_snapshot("__healthcheck__")
        return ReadinessCheck(configured=True)
    except Exception as exc:  # noqa: BLE001 - readiness wants any failure surfaced, not raised
        return ReadinessCheck(configured=False, detail=f"Checkpointer not reachable: {exc}")


def _check_anthropic_live() -> ReadinessCheck:
    try:
        import anthropic

        anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY")).models.list(limit=1)
        return ReadinessCheck(configured=True, detail="live check passed")
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck(configured=False, detail=f"Anthropic live check failed: {exc}")


def _check_supabase_live() -> ReadinessCheck:
    try:
        from invoice_agent import db

        db.get_client().table("invoices").select("id").limit(1).execute()
        return ReadinessCheck(configured=True, detail="live check passed")
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck(configured=False, detail=f"Supabase live check failed: {exc}")


def check_readiness(service: GraphService, *, deep: bool = False) -> ReadinessResponse:
    """Config-only by default - see module docstring for why. `deep=True`
    additionally makes one live call per already-configured service.
    """
    checks = {
        "anthropic": _check_anthropic_config(),
        "supabase": _check_supabase_config(),
        "langfuse": _check_langfuse_config(),
        "checkpointer": _check_checkpointer(service),
    }
    if deep:
        if checks["anthropic"].configured:
            checks["anthropic"] = _check_anthropic_live()
        if checks["supabase"].configured:
            checks["supabase"] = _check_supabase_live()

    # Langfuse doesn't gate overall status - tracing is optional by design
    # (invoice_agent/tracing.py degrades to a no-op without credentials).
    # Anthropic/Supabase/the checkpointer are hard requirements for the
    # pipeline to do anything, so only those three gate "degraded".
    status = "ok" if all(checks[name].configured for name in _REQUIRED_CHECKS) else "degraded"
    return ReadinessResponse(status=status, checks=checks)
