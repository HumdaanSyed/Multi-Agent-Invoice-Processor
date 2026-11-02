"""Shared test setup.

Most API tests exercise invoice behaviour, not authentication, so they run
with AUTH_DISABLED=true as the allowlisted owner. tests/test_auth.py turns
it back off and checks real JWT verification and authorization.
"""

import pytest

TEST_OWNER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _auth_as_owner(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("OWNER_USER_ID", TEST_OWNER_ID)
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
