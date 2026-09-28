"""API-adapter integration tests — entry auth (non-HITL, single-tenant template).

Exercises src/api/server.py over HTTP (FastAPI TestClient). This template has no
HITL/resume and is single-tenant-per-deployment (tenant_id selects a KB
partition within the one deployment's isolated store — not a cross-tenant
capability). The retrofit adds only entry auth: an unauthenticated caller must be
rejected 401 (enum/string ANONYMOUS), and an authenticated one succeeds.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel

_TOKEN = "test-invoke-token-svc029"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    import importlib

    import src.api.server as server

    importlib.reload(server)
    return TestClient(server.app), server


def _auth(token: str = _TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _body(session_id="sess-1"):
    return {"input": "大阪 出張 ホテル", "session_id": session_id,
            "input_context": {"mode": "recommend", "destination": "大阪", "per_diem": 11000,
                              "tier": "standard", "tenant_id": "acme"}}


def test_invoke_without_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_body()).status_code == 401


def test_invoke_wrong_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_body(), headers=_auth("nope")).status_code == 401


@pytest.mark.parametrize("stamp", [TrustLevel.ANONYMOUS, "ANONYMOUS"])
def test_invoke_explicit_anonymous_middleware_is_401(client, stamp):
    """The old fallback that defaulted callers to ANONYMOUS-and-through is gone —
    a middleware stamping ANONYMOUS (enum or string) is now rejected 401."""
    c, server = client

    @server.app.middleware("http")
    async def _stamp(request, call_next):
        request.state.trust_level = stamp
        return await call_next(request)

    fresh = TestClient(server.app)
    assert fresh.post("/invoke", json=_body()).status_code == 401


def test_invoke_good_token_succeeds(client):
    c, _ = client
    r = c.post("/invoke", json=_body(), headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "success"
