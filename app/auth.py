"""Request authentication and authorization for the FastAPI backend.

Every protected request carries a Supabase access token as
`Authorization: Bearer <jwt>` (the Next.js /api proxy forwards the signed-in
user's token; see web/app/api/[...path]/route.ts). This module verifies it
*independently* of the frontend: signature against the project's public
JWKS (asymmetric keys only, so a leaked publishable key can't mint tokens),
expiry, issuer and audience. The user identity is the token's verified
`sub` claim - never a header or body field a client could set.

Three levels, used as FastAPI dependencies:
  - current_user: any valid token                 -> else 401
  - require_allowed: user is in the app allowlist -> else 403
  - require_owner: user is OWNER_USER_ID          -> else 403 (Gmail, ledger)

Config (env): SUPABASE_URL, OWNER_USER_ID, optional ALLOWED_USER_IDS
(comma-separated; defaults to just the owner). AUTH_DISABLED=true skips
verification for local development and tests only - it logs a warning and
must never be set in a deployment.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request

from app.errors import Forbidden, NotAuthenticated, UpstreamUnavailable

logger = logging.getLogger("app.auth")

# Supabase's asymmetric signing keys are ES256 (default) or RS256. HS256 is
# deliberately excluded: accepting it would let anyone holding the legacy
# shared secret mint tokens.
ALGORITHMS = ["ES256", "RS256"]
AUDIENCE = "authenticated"


@dataclass(frozen=True)
class User:
    id: str


def owner_id() -> str | None:
    return os.environ.get("OWNER_USER_ID") or None


def allowed_ids() -> set[str]:
    ids = {i.strip() for i in os.environ.get("ALLOWED_USER_IDS", "").split(",") if i.strip()}
    owner = owner_id()
    return ids | {owner} if owner else ids


def _auth_disabled() -> bool:
    return os.environ.get("AUTH_DISABLED", "").lower() == "true"


@functools.lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Caches the key set in-process (refetched on an unknown `kid`, so key
    # rotation works without a restart).
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=5)


def _issuer() -> str:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not url:
        logger.error("SUPABASE_URL is not set - can't verify access tokens.")
        raise UpstreamUnavailable("Sign-in isn't configured on the server.")
    return f"{url}/auth/v1"


def verify_token(token: str) -> User:
    issuer = _issuer()
    try:
        signing_key = _jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=issuer,
            # Tolerates small clock skew vs Supabase (PyJWT rejects a future iat).
            leeway=30,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        logger.warning("JWKS fetch failed: %s", exc.__class__.__name__)
        raise UpstreamUnavailable("Couldn't verify the session right now. Try again shortly.") from exc
    except jwt.PyJWTError as exc:
        # Log only the failure class (expired, bad signature...), never the token.
        logger.info("Rejected access token: %s", exc.__class__.__name__)
        raise NotAuthenticated("Sign in to continue.") from exc
    return User(id=str(claims["sub"]))


def current_user(request: Request) -> User:
    if _auth_disabled():
        # Warned once at startup (app/main.py), not per request.
        return User(id=owner_id() or "local-dev")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise NotAuthenticated("Sign in to continue.")
    return verify_token(token.strip())


def require_allowed(user: User = Depends(current_user)) -> User:
    if user.id not in allowed_ids():
        raise Forbidden("This account doesn't have access.")
    return user


def require_owner(user: User = Depends(require_allowed)) -> User:
    if user.id != owner_id():
        raise Forbidden("This account doesn't have access.")
    return user
