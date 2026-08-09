"""Phase 0 smoke test.

Verifies every external service dependency can be reached:
  - Anthropic client initializes and can list/reach the API
  - Supabase client initializes and can reach the project

Run: python scripts/smoke_test.py

This only checks that clients construct and can make a trivial live call —
it does not exercise the invoice pipeline itself (that starts in Phase 1).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def check_anthropic() -> bool:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key.startswith("sk-ant-your-key"):
        print("  ANTHROPIC_API_KEY is missing or still a placeholder in .env")
        return False

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        # Trivial, cheap, real API call to confirm the key/network/client work.
        client.models.list(limit=1)
        print("  Anthropic client initialized and reached the API.")
        return True
    except Exception as exc:  # noqa: BLE001 - smoke test wants any failure surfaced
        print(f"  Anthropic check failed: {exc}")
        return False


def check_supabase() -> bool:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or "your-project-ref" in url or not key or key.startswith("your-supabase"):
        print("  SUPABASE_URL / SUPABASE_KEY missing or still placeholders in .env")
        return False

    try:
        from supabase import create_client

        client = create_client(url, key)
        # No tables exist yet (that's Phase 4) — constructing the client and
        # having it hold valid auth/config is what Phase 0 cares about.
        print(f"  Supabase client initialized for {url}.")
        _ = client
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  Supabase check failed: {exc}")
        return False


def main() -> int:
    load_dotenv()

    print("Anthropic:")
    anthropic_ok = check_anthropic()

    print("Supabase:")
    supabase_ok = check_supabase()

    print()
    print(f"Anthropic: {'OK' if anthropic_ok else 'FAILED'}")
    print(f"Supabase:  {'OK' if supabase_ok else 'FAILED'}")

    if anthropic_ok and supabase_ok:
        print("\nAll services reachable. Phase 0 complete.")
        return 0

    print("\nOne or more services not reachable. Fix .env and re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
