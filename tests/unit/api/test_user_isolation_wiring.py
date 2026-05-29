"""End-to-end wiring test: the auth-gate middleware enforces per-user project
isolation over real HTTP (forged JWT cookies for two users)."""
import base64
import importlib
import json
import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _cookie_for(user_id: str) -> str:
    """Forge a minimal JWT the gate will accept: header.payload.sig, payload
    carries userId + a future exp (the gate only base64-decodes the payload)."""
    hdr = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = json.dumps({"userId": user_id, "exp": time.time() + 3600})
    pay = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{hdr}.{pay}.sig"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("AUTH_BACKEND", "http://unused.local")
    monkeypatch.setenv("AUTH_CLIENT_ID", "test")
    monkeypatch.delenv("OMC_TRUST_LOCALHOST", raising=False)  # so localhost isn't auto-trusted

    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "DATA_ROOT", tmp_path)
    import onemancompany.core.user_llm as user_llm
    importlib.reload(user_llm)
    user_llm._OWNERS_FILE = tmp_path / "project_owners.json"
    user_llm._KEYS_FILE = tmp_path / "user_llm_keys.json"
    # alice owns p_alice, bob owns p_bob
    user_llm.set_project_owner("p_alice", "alice")
    user_llm.set_project_owner("p_bob", "bob")

    import onemancompany.api.auth_gate as gate
    importlib.reload(gate)

    # Minimal app: a per-project route the middleware should guard.
    async def project_status(request):
        return JSONResponse({"pid": request.path_params["pid"], "ok": True})

    app = Starlette(routes=[
        Route("/api/pipeline/{pid}/status", project_status),
    ])
    gate.install_auth_gate(app)
    return TestClient(app), gate


def test_owner_can_access_own_project(client):
    c, gate = client
    r = c.get("/api/pipeline/p_alice/status", cookies={gate.COOKIE: _cookie_for("alice")})
    assert r.status_code == 200 and r.json()["pid"] == "p_alice"


def test_user_blocked_from_others_project(client):
    c, gate = client
    r = c.get("/api/pipeline/p_bob/status", cookies={gate.COOKIE: _cookie_for("alice")})
    assert r.status_code == 403
    assert "another user" in r.json()["detail"]


def test_no_cookie_redirected_or_401(client):
    c, gate = client
    r = c.get("/api/pipeline/p_alice/status", follow_redirects=False)
    assert r.status_code in (302, 401)
