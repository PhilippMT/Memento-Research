"""Unit tests for the memento asset tool.

Patches `MemoryV4Adapter` to avoid real LLM calls. Six tests cover
the safety + happy-path surface; full live-LLM regression suite
lives outside this PR.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from onemancompany.core.vessel import _current_vessel


@pytest.fixture
def fake_vessel():
    return SimpleNamespace(employee_id="E00006")


@pytest.fixture
def employee_root(tmp_path, monkeypatch):
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    (employees_dir / "E00006").mkdir()
    monkeypatch.setattr(
        "onemancompany.core.config.EMPLOYEES_DIR", employees_dir, raising=False
    )
    import company.assets.tools.memento.memento as memento_mod
    monkeypatch.setattr(memento_mod, "EMPLOYEES_DIR", employees_dir, raising=False)
    return employees_dir


@contextmanager
def _with_vessel(vessel):
    token = _current_vessel.set(vessel)
    try:
        yield
    finally:
        _current_vessel.reset(token)


def test_store_requires_employee_context(employee_root):
    """No vessel → store returns error (isolation invariant)."""
    from company.assets.tools.memento.memento import store

    result = store.invoke({"turns": [{"role": "user", "content": "hi"}]})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_recall_requires_employee_context(employee_root):
    """No vessel → recall returns error (isolation invariant)."""
    from company.assets.tools.memento.memento import recall

    result = recall.invoke({"query": "anything"})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_store_rejects_invalid_turns(employee_root, fake_vessel):
    """Empty turns + invalid role both rejected with status=error."""
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        empty = store.invoke({"turns": []})
        invalid = store.invoke({"turns": [{"role": "system", "content": "hi"}]})

    assert empty["status"] == "error"
    assert "non-empty" in empty["message"].lower()
    assert invalid["status"] == "error"
    assert "role" in invalid["message"].lower()


def test_store_happy_path_writes_session(employee_root, fake_vessel, monkeypatch):
    """store writes session JSON, calls adapter.ingest, returns ok."""
    from company.assets.tools.memento import memento as memento_mod

    captured = {}

    class _FakeAdapter:
        def __init__(self, **kw):
            captured["init"] = kw

        async def setup(self):
            pass

        async def ingest(self, conv, conv_id):
            captured["conv_id"] = conv_id
            captured["session_count"] = len(conv.sessions)

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _FakeAdapter)

    with _with_vessel(fake_vessel):
        result = memento_mod.store.invoke({
            "turns": [
                {"role": "user", "content": "find auth bug"},
                {"role": "assistant", "content": "reproduced AUTH-742"},
            ]
        })

    assert result["status"] == "ok", result
    assert result["session_num"] == 1
    assert result["session_id"].endswith("_sess1")
    assert captured["conv_id"] == "E00006"

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = sorted(sessions_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["turns"][0]["content"] == "find auth bug"


def test_recall_after_store_returns_context(employee_root, fake_vessel, monkeypatch):
    """recall returns the patched RecallContext from the adapter."""
    from company.assets.tools.memento import memento as memento_mod
    from company.assets.tools.memento.memento import RecallContext

    class _NoopStore:
        def __init__(self, **_): pass
        async def setup(self): pass
        async def ingest(self, *_a, **_kw): pass

    class _RecallAdapter(_NoopStore):
        async def recall(self, query, conv_id):
            return RecallContext(
                raw_text=f"## Mocked context for '{query}'",
                session_ids=["convE00006_sess1"],
                metadata={},
            )

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _NoopStore)
    with _with_vessel(fake_vessel):
        memento_mod.store.invoke({"turns": [{"role": "user", "content": "hello"}]})

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _RecallAdapter)
    with _with_vessel(fake_vessel):
        result = memento_mod.recall.invoke({"query": "hi", "top_k": 3})

    assert result["status"] == "ok"
    assert result["query"] == "hi"
    assert "Mocked context" in result["context"]
    assert result["session_ids"] == ["convE00006_sess1"]


def test_isolation_two_employees(employee_root, monkeypatch):
    """E00006 store invisible to E00007 (filesystem-level isolation)."""
    from company.assets.tools.memento import memento as memento_mod
    from company.assets.tools.memento.memento import RecallContext

    (employee_root / "E00007").mkdir(exist_ok=True)

    class _StoreAdapter:
        def __init__(self, **_): pass
        async def setup(self): pass
        async def ingest(self, *_a, **_kw): pass

    class _RecallEmpty(_StoreAdapter):
        async def recall(self, *_a, **_kw):
            return RecallContext(raw_text="", session_ids=[], metadata={})

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _StoreAdapter)
    with _with_vessel(SimpleNamespace(employee_id="E00006")):
        memento_mod.store.invoke({
            "turns": [{"role": "user", "content": "Acme uses SAML"}]
        })

    e6 = list((employee_root / "E00006" / "memory" / "sessions").glob("*.json"))
    e7_dir = employee_root / "E00007" / "memory" / "sessions"
    assert len(e6) == 1
    assert not e7_dir.exists() or not list(e7_dir.glob("*.json"))

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _RecallEmpty)
    with _with_vessel(SimpleNamespace(employee_id="E00007")):
        result = memento_mod.recall.invoke({"query": "Acme"})

    assert result["status"] == "ok"
    assert "no prior sessions" in result["context"].lower()
    assert result["session_ids"] == []


def test_store_finalize_failure_preserves_transcript(employee_root, fake_vessel, monkeypatch):
    """If finalize raises, the session JSON on disk is still written."""
    from company.assets.tools.memento import memento as memento_mod

    class _CrashingAdapter:
        def __init__(self, **_): pass
        async def setup(self): pass
        async def ingest(self, *_a, **_kw):
            raise RuntimeError("simulated finalize crash")

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _CrashingAdapter)

    with _with_vessel(fake_vessel):
        result = memento_mod.store.invoke({
            "turns": [{"role": "user", "content": "important fact"}]
        })

    assert result["status"] == "error"
    assert "finalize" in result["message"].lower()

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = list(sessions_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["turns"][0]["content"] == "important fact"
