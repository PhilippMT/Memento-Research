"""Per-user project isolation (each logged-in user sees only their own context)."""
import importlib
import pytest


@pytest.fixture
def u(tmp_path, monkeypatch):
    import onemancompany.core.config as cfg
    monkeypatch.setattr(cfg, "DATA_ROOT", tmp_path)
    import onemancompany.core.user_llm as user_llm
    importlib.reload(user_llm)
    user_llm._OWNERS_FILE = tmp_path / "project_owners.json"
    user_llm._KEYS_FILE = tmp_path / "user_llm_keys.json"
    return user_llm


def test_filter_projects_isolates_by_owner(u):
    u.set_project_owner("p1", "alice")
    u.set_project_owner("p2", "bob")
    projs = [{"project_id": "p1"}, {"project_id": "p2"}, {"project_id": "p3"}]
    assert [p["project_id"] for p in u.filter_projects_for_user(projs, "alice")] == ["p1"]
    assert [p["project_id"] for p in u.filter_projects_for_user(projs, "bob")] == ["p2"]


def test_filter_projects_auth_off_returns_all(u):
    u.set_project_owner("p1", "alice")
    projs = [{"project_id": "p1"}, {"project_id": "p2"}]
    # Empty user id (auth disabled / localhost automation) → no filtering.
    assert u.filter_projects_for_user(projs, "") == projs


def test_filter_projects_keys_by_base_pid(u):
    u.set_project_owner("p1/iter_001", "alice")
    projs = [{"project_id": "p1/iter_003"}, {"id": "p2"}]
    assert [p.get("project_id") for p in u.filter_projects_for_user(projs, "alice")] == ["p1/iter_003"]


def test_access_guard(u):
    u.set_project_owner("p1", "alice")
    assert u.user_can_access_project("p1", "alice") is True
    assert u.user_can_access_project("p1", "bob") is False
    assert u.user_can_access_project("p1", "") is True   # auth off → allowed
    assert u.user_can_access_project("p1/iter_002", "alice") is True  # base-pid keyed


@pytest.mark.asyncio
async def test_resume_breakpoint_rejects_foreign_owner(u, monkeypatch):
    """The body-keyed /api/pipeline/resume route can't be guarded by the path
    regex, so it must enforce ownership in-handler: a logged-in user who is not
    the project owner gets 403 BEFORE any resume work happens."""
    from fastapi import HTTPException
    import onemancompany.api.routes as routes

    u.set_project_owner("p1", "alice")
    # auth_gate.current_user_id reads the JWT; force the caller to be "bob".
    import onemancompany.api.auth_gate as auth_gate
    monkeypatch.setattr(auth_gate, "current_user_id", lambda _req: "bob")

    with pytest.raises(HTTPException) as ei:
        await routes.resume_pipeline_breakpoint({"project_id": "p1", "stage": 3}, request=object())
    assert ei.value.status_code == 403


def test_project_id_from_path():
    from onemancompany.api.auth_gate import _project_id_from_path as f
    assert f("/api/pipeline/abc123/status") == "abc123"
    assert f("/api/task/xy9/abort") == "xy9"
    assert f("/api/projects/named/pp1") == "pp1"
    assert f("/api/projects/pp2/files/x.md") == "pp2"
    # list / non-project routes must NOT be treated as a project id
    assert f("/api/projects") == ""
    assert f("/api/projects/named") == ""
    assert f("/api/bootstrap") == ""
    assert f("/api/me") == ""
    # /api/pipeline/resume is a REAL body-keyed action route: its project id
    # lives in the request BODY, so the path regex must NOT capture "resume" as
    # a pid — else the owner-guard 403s the gate-advance call (the regression
    # this fix addresses). Ownership for /resume is enforced in-handler instead.
    assert f("/api/pipeline/resume") == ""
    # Pure regex-shape negatives: a bare verb segment with no trailing /<sub>
    # must never be treated as a project id. (These exact paths are not real
    # routes — the real ones are /api/pipeline/{pid}/revert and
    # /api/admin/clear-tasks — they only assert the regex requires a sub-path.)
    assert f("/api/pipeline/revert") == ""
    assert f("/api/task/clear") == ""
