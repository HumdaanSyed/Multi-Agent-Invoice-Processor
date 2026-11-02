"""Authentication and authorization (app/auth.py, app/owners.py) with real
ES256-signed JWTs. Only the JWKS fetch is faked: tokens are signed with a
throwaway key pair and verified by the same PyJWT code production runs."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

import app.auth as auth
import app.inbox as inbox
from tests.conftest import TEST_OWNER_ID
from tests.test_api_routes import GOOD_INVOICE, _make_client, _upload

SUPABASE_URL = "https://testproj.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
OTHER_ID = "22222222-2222-2222-2222-222222222222"  # allowlisted, but not the owner
STRANGER_ID = "33333333-3333-3333-3333-333333333333"  # valid account, not allowlisted

SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
ROGUE_KEY = ec.generate_private_key(ec.SECP256R1())


class _FakeJWKS:
    """Stands in for PyJWKClient: always answers with the project's public key."""

    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(jwt.algorithms.ECAlgorithm.to_jwk(SIGNING_KEY.public_key(), as_dict=True) | {"alg": "ES256"})


def token(sub: str = TEST_OWNER_ID, *, key=SIGNING_KEY, alg="ES256", **overrides) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": ISSUER, "aud": "authenticated", "iat": now, "exp": now + 3600, "role": "authenticated"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test-key"})


def bearer(sub: str = TEST_OWNER_ID, **kwargs) -> dict:
    return {"Authorization": f"Bearer {token(sub, **kwargs)}"}


@pytest.fixture(autouse=True)
def _real_auth(_auth_as_owner, monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED")
    monkeypatch.setenv("ALLOWED_USER_IDS", OTHER_ID)
    monkeypatch.setattr(auth, "_jwks_client", lambda url: _FakeJWKS())
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "ANTHROPIC_API_KEY", "SUPABASE_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Set after _make_client's fakes: the issuer is derived from this.
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)


@pytest.fixture
def client(monkeypatch):
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as c:
        yield c


# --- token verification ------------------------------------------------------


def test_valid_owner_token_is_accepted(client):
    assert client.get("/invoices", headers=bearer()).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": f"Token {token()}"},
    ],
    ids=["missing", "empty-bearer", "malformed", "basic-scheme", "wrong-scheme"],
)
def test_missing_or_malformed_token_is_401(client, headers):
    response = client.get("/invoices", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": "not_authenticated", "message": "Sign in to continue."}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exp": int(time.time()) - 60},
        {"iss": "https://evil.supabase.co/auth/v1"},
        {"aud": "anon"},
        {"key": ROGUE_KEY},
        {"exp": None},
        {"sub": None},
    ],
    ids=["expired", "wrong-issuer", "wrong-audience", "wrong-signing-key", "no-exp", "no-sub"],
)
def test_invalid_tokens_are_401(client, kwargs):
    sub = kwargs.pop("sub", TEST_OWNER_ID)
    headers = {"Authorization": f"Bearer {token(sub, **kwargs)}"}
    assert client.get("/invoices", headers=headers).status_code == 401


def test_unsigned_alg_none_token_is_401(client):
    unsigned = jwt.encode({"sub": TEST_OWNER_ID, "iss": ISSUER, "aud": "authenticated", "exp": int(time.time()) + 60}, None, algorithm="none")
    assert client.get("/invoices", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401


def test_symmetric_hs256_token_is_401(client):
    # A token signed with a shared secret (the legacy Supabase JWT secret, or
    # an attacker guessing one) must not pass: only the asymmetric algs do.
    forged = jwt.encode(
        {"sub": TEST_OWNER_ID, "iss": ISSUER, "aud": "authenticated", "iat": int(time.time()), "exp": int(time.time()) + 60},
        "a-shared-secret-of-at-least-32-bytes!!",
        algorithm="HS256",
    )
    assert client.get("/invoices", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_spoofed_identity_headers_are_ignored(client):
    # No token, just headers a misconfigured proxy might trust.
    headers = {"X-User-Id": TEST_OWNER_ID, "X-Forwarded-User": TEST_OWNER_ID, "X-Supabase-User": TEST_OWNER_ID}
    assert client.get("/invoices", headers=headers).status_code == 401


def test_valid_token_for_non_allowlisted_user_is_403(client):
    response = client.get("/invoices", headers=bearer(STRANGER_ID))
    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "message": "This account doesn't have access."}


def test_no_owner_configured_fails_closed(client, monkeypatch):
    monkeypatch.delenv("OWNER_USER_ID")
    monkeypatch.delenv("ALLOWED_USER_IDS")
    assert client.get("/invoices", headers=bearer()).status_code == 403


def test_jwks_outage_is_503_not_401(client, monkeypatch):
    class Down:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("connection refused to jwks host")

    monkeypatch.setattr(auth, "_jwks_client", lambda url: Down())
    response = client.get("/invoices", headers=bearer())
    assert response.status_code == 503
    assert "jwks" not in response.text.lower()


def test_auth_errors_never_echo_the_token(client):
    bad = token(exp=int(time.time()) - 60)
    response = client.get("/invoices", headers={"Authorization": f"Bearer {bad}"})
    assert bad not in response.text
    assert "expired" not in response.text.lower()  # no verification detail either


# --- health -------------------------------------------------------------------


def test_liveness_is_public_and_minimal(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_deep_readiness_is_owner_only(client):
    assert client.get("/health/ready").status_code == 401
    assert client.get("/health/ready", headers=bearer(OTHER_ID)).status_code == 403
    assert client.get("/health/ready", headers=bearer()).status_code in (200, 503)  # 503 = degraded, no creds in tests


# --- upload, stream, resume, details: owner-scoped runs ------------------------


def _upload_as(client, sub: str, **data):
    return client.post(
        "/invoices",
        headers=bearer(sub),
        files={"file": ("invoice.pdf", b"%PDF-1.4\nfake pdf content", "application/pdf")},
        data=data,
    )


def test_upload_requires_auth(client):
    assert _upload(client).status_code == 401
    assert client.post("/invoices/reserve").status_code == 401


def test_run_endpoints_require_auth(client):
    thread_id = _upload_as(client, TEST_OWNER_ID).json()["thread_id"]
    assert client.get(f"/invoices/{thread_id}").status_code == 401
    assert client.get(f"/invoices/{thread_id}/stream").status_code == 401
    assert client.post(f"/invoices/{thread_id}/resume", json={"corrections": {}}).status_code == 401


def test_owner_can_read_own_run(client):
    thread_id = _upload_as(client, TEST_OWNER_ID).json()["thread_id"]
    assert client.get(f"/invoices/{thread_id}", headers=bearer()).status_code == 200
    with client.stream("GET", f"/invoices/{thread_id}/stream", headers=bearer()) as response:
        assert response.status_code == 200


def test_other_user_cannot_touch_someone_elses_run(client):
    thread_id = _upload_as(client, TEST_OWNER_ID).json()["thread_id"]
    other = bearer(OTHER_ID)

    for response in (
        client.get(f"/invoices/{thread_id}", headers=other),
        client.get(f"/invoices/{thread_id}/stream", headers=other),
        client.post(f"/invoices/{thread_id}/resume", headers=other, json={"corrections": {}}),
    ):
        assert response.status_code == 403
        # Nothing about the run leaks into the refusal.
        assert response.json() == {"error": "forbidden", "message": "You don't have access to this run."}


def test_run_list_is_scoped_to_the_caller(client):
    mine = _upload_as(client, TEST_OWNER_ID).json()["thread_id"]
    theirs = _upload_as(client, OTHER_ID).json()["thread_id"]

    owner_ids = {r["thread_id"] for r in client.get("/invoices", headers=bearer()).json()["runs"]}
    other_ids = {r["thread_id"] for r in client.get("/invoices", headers=bearer(OTHER_ID)).json()["runs"]}
    assert mine in owner_ids and theirs not in owner_ids
    assert other_ids == {theirs}


def test_cannot_upload_into_someone_elses_reserved_thread(client):
    reserved = client.post("/invoices/reserve", headers=bearer()).json()["thread_id"]
    assert _upload_as(client, OTHER_ID, thread_id=reserved).status_code == 403


def test_unknown_thread_is_403_not_a_probe(client):
    # An id nobody claimed belongs to the owner (legacy runs predate owner
    # scoping), so other users get the same 403 whether it exists or not.
    assert client.get("/invoices/does-not-exist", headers=bearer(OTHER_ID)).status_code == 403


# --- export: owner only ------------------------------------------------------


@pytest.mark.parametrize("path", ["/export.csv", "/export/status"])
def test_export_is_owner_only(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bearer(OTHER_ID)).status_code == 403
    assert client.get(path, headers=bearer(STRANGER_ID)).status_code == 403


# --- Gmail / inbox: owner only --------------------------------------------------


@pytest.fixture
def mcp_calls(monkeypatch):
    calls = []

    async def fake(source, tool, args):
        calls.append((source, tool, args))
        return ([] if tool != "mark_processed" else [{"type": "text", "text": "true"}]), 1

    monkeypatch.setattr(inbox, "call_tool", fake)
    return calls


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/inbox", None),
        ("POST", "/inbox/download", {"source": "gmail", "id": "101"}),
        ("POST", "/inbox/mark", {"source": "gmail", "id": "101"}),
        ("GET", "/inbox/files/101_invoice.pdf", None),
    ],
)
def test_gmail_endpoints_owner_only(client, mcp_calls, method, path, body):
    def call(headers):
        return client.request(method, path, headers=headers, json=body)

    assert call({}).status_code == 401
    assert call(bearer(OTHER_ID)).status_code == 403
    assert call(bearer(STRANGER_ID)).status_code == 403
    assert mcp_calls == []  # refused before the mailbox is ever touched
    assert call(bearer()).status_code in (200, 404)  # 404: no downloaded file in a fresh test dir


def test_small_clock_skew_is_tolerated(client):
    # Token issued "10s in the future" relative to this server's clock.
    assert client.get("/invoices", headers=bearer(iat=int(time.time()) + 10)).status_code == 200


def test_missing_supabase_url_is_503_not_500(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL")
    assert client.get("/invoices", headers=bearer()).status_code == 503


def test_scoped_list_widens_past_other_users_checkpoints(client):
    # Other users' newer checkpoints fill a tiny window; the caller's older run must still be found.
    theirs = _upload_as(client, OTHER_ID).json()["thread_id"]
    for _ in range(3):
        _upload_as(client, TEST_OWNER_ID)
    owners = client.app.state.owners
    other = auth.User(OTHER_ID)
    rows = client.app.state.graph_service.list_runs(limit=5, overfetch=2, visible=lambda t: owners.can_access(t, other))
    assert [r["thread_id"] for r in rows] == [theirs]
