"""Coverage for ``onemancompany.core.user_llm`` — the per-user LLM key store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from onemancompany.core import user_llm


@pytest.fixture
def isolated_stores(tmp_path: Path, monkeypatch) -> Path:
    """Point both JSON stores at a temp dir so tests don't touch DATA_ROOT."""
    monkeypatch.setattr(user_llm, "_KEYS_FILE", tmp_path / "user_llm_keys.json")
    monkeypatch.setattr(user_llm, "_OWNERS_FILE", tmp_path / "project_owners.json")
    return tmp_path


def test_read_json_missing_file_returns_empty():
    assert user_llm._read_json(Path("/nonexistent/does-not-exist.json")) == {}


def test_read_json_corrupt_file_logs_and_returns_empty(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert user_llm._read_json(bad) == {}


def test_write_json_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "file.json"
    user_llm._write_json(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_set_and_get_project_owner_round_trip(isolated_stores):
    user_llm.set_project_owner("proj-1/iter_001", "user-42")
    assert user_llm.get_project_owner("proj-1") == "user-42"
    # Base pid stripping works on both sides.
    assert user_llm.get_project_owner("proj-1/iter_002") == "user-42"


def test_set_project_owner_no_op_on_empty_inputs(isolated_stores):
    user_llm.set_project_owner("", "user-42")
    user_llm.set_project_owner("proj-1", "")
    assert user_llm.get_project_owner("proj-1") == ""


def test_resolve_user_llm_missing_user_returns_none(isolated_stores):
    assert user_llm.resolve_user_llm("") is None
    assert user_llm.resolve_user_llm("ghost") is None


def test_resolve_user_llm_returns_entry_when_api_key_set(isolated_stores):
    user_llm._write_json(
        user_llm._KEYS_FILE,
        {"user-42": {"api_key": "sk-test", "base_url": "https://x", "model": "m"}},
    )
    entry = user_llm.resolve_user_llm("user-42")
    assert entry == {"api_key": "sk-test", "base_url": "https://x", "model": "m"}


def test_resolve_user_llm_ignores_entry_without_api_key(isolated_stores):
    user_llm._write_json(user_llm._KEYS_FILE, {"user-42": {"model": "m"}})
    assert user_llm.resolve_user_llm("user-42") is None


def test_resolve_for_project_threads_owner_lookup(isolated_stores):
    user_llm.set_project_owner("proj-1", "user-42")
    user_llm._write_json(
        user_llm._KEYS_FILE, {"user-42": {"api_key": "sk-test"}}
    )
    assert user_llm.resolve_for_project("proj-1/iter_001") == {"api_key": "sk-test"}
    assert user_llm.resolve_for_project("unknown-proj") is None


# ---------------------------------------------------------------------------
# reset_session — daemon cleanup branches
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, *, alive: bool = True, terminate_raises: bool = False):
        self.returncode = None if alive else 0
        self.terminated = False
        self._raise = terminate_raises

    def terminate(self):
        if self._raise:
            raise ProcessLookupError("gone")
        self.terminated = True


class _FakeTask:
    def __init__(self, done: bool = False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


def test_reset_session_terminates_live_daemon_and_drops_entry(tmp_path, monkeypatch):
    from onemancompany.core import claude_session as cs
    proc = _FakeProc(alive=True)
    task = _FakeTask(done=False)
    daemon = type("D", (), {})()
    daemon.proc = proc
    daemon._stderr_task = task
    cs._daemons["emp1:proj1"] = daemon

    monkeypatch.setattr(cs, "_load_sessions", lambda eid: {"proj1": {"session_id": "abc"}})
    saved = {}
    monkeypatch.setattr(cs, "_save_sessions", lambda eid, d: saved.update({"eid": eid, "data": d}))

    cs.reset_session("emp1", "proj1")

    assert "emp1:proj1" not in cs._daemons
    assert task.cancelled is True
    assert proc.terminated is True
    assert daemon.proc is None
    # Stored session for this project is removed.
    assert saved == {"eid": "emp1", "data": {}}


def test_reset_session_swallows_process_lookup_error(tmp_path, monkeypatch):
    from onemancompany.core import claude_session as cs
    proc = _FakeProc(alive=True, terminate_raises=True)
    daemon = type("D", (), {})()
    daemon.proc = proc
    daemon._stderr_task = None  # no stderr task → skip cancel branch
    cs._daemons["emp1:proj2"] = daemon

    monkeypatch.setattr(cs, "_load_sessions", lambda eid: {})
    monkeypatch.setattr(cs, "_save_sessions", lambda eid, d: None)

    # Must NOT propagate ProcessLookupError.
    cs.reset_session("emp1", "proj2")
    assert "emp1:proj2" not in cs._daemons


def test_reset_session_no_op_when_no_daemon(monkeypatch):
    from onemancompany.core import claude_session as cs
    cs._daemons.pop("emp1:noop", None)
    monkeypatch.setattr(cs, "_load_sessions", lambda eid: {})
    monkeypatch.setattr(cs, "_save_sessions", lambda eid, d: None)
    cs.reset_session("emp1", "noop")  # must not raise
