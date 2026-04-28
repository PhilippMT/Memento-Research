"""Unit tests for the memento asset tool.

The tool exposes two LangChain @tool functions: store and recall. These
tests use a fake Vessel set on the ContextVar to drive employee context,
and patch MemoryV4Adapter to avoid real LLM calls.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
def _with_vessel(fake_vessel):
    token = _current_vessel.set(fake_vessel)
    try:
        yield
    finally:
        _current_vessel.reset(token)


def test_store_requires_employee_context(employee_root):
    from company.assets.tools.memento.memento import store

    result = store.invoke({"turns": [{"role": "user", "content": "hi"}]})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_recall_requires_employee_context(employee_root):
    from company.assets.tools.memento.memento import recall

    result = recall.invoke({"query": "anything"})

    assert result["status"] == "error"
    assert "employee context" in result["message"].lower()


def test_store_rejects_empty_turns(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({"turns": []})

    assert result["status"] == "error"
    assert "non-empty" in result["message"].lower()


def test_store_rejects_non_list_turns(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        # LangChain @tool runs Pydantic validation that may reject a string
        # before our handler — accept either a tool error or our own error.
        try:
            result = store.invoke({"turns": "not a list"})
        except Exception as exc:
            assert "list" in str(exc).lower() or "valid" in str(exc).lower()
            return

    assert result["status"] == "error"
    assert "list" in result["message"].lower()


def test_store_rejects_turn_missing_role(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({"turns": [{"content": "hi"}]})

    assert result["status"] == "error"
    assert "role" in result["message"].lower()


def test_store_rejects_invalid_role(employee_root, fake_vessel):
    from company.assets.tools.memento.memento import store

    with _with_vessel(fake_vessel):
        result = store.invoke({
            "turns": [{"role": "system", "content": "hi"}]
        })

    assert result["status"] == "error"
    assert "invalid role" in result["message"].lower() or "role" in result["message"].lower()


def test_store_happy_path_patches_adapter(employee_root, fake_vessel, monkeypatch):
    """store writes the session JSON, then ingests via the adapter."""
    from company.assets.tools.memento import memento as memento_mod

    captured = {}

    class _FakeAdapter:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self._memory_root = kwargs["memory_root"]

        async def setup(self):
            captured["setup_called"] = True

        async def ingest(self, conv, conv_id):
            captured["ingest_conv_id"] = conv_id
            captured["ingest_session_count"] = len(conv.sessions)

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

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = sorted(sessions_dir.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["session_num"] == 1
    assert len(payload["turns"]) == 2
    assert payload["turns"][0]["content"] == "find auth bug"

    assert captured["ingest_conv_id"] == "E00006"
    assert captured["ingest_session_count"] == 1


def test_store_increments_session_num(employee_root, fake_vessel, monkeypatch):
    """Three consecutive stores produce session_nums 1, 2, 3 with 001/002/003.json."""
    from company.assets.tools.memento import memento as memento_mod

    class _NoopAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _NoopAdapter)

    with _with_vessel(fake_vessel):
        r1 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task one"}]})
        r2 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task two"}]})
        r3 = memento_mod.store.invoke({"turns": [{"role": "user", "content": "task three"}]})

    assert r1["session_num"] == 1
    assert r2["session_num"] == 2
    assert r3["session_num"] == 3

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    written = sorted(p.name for p in sessions_dir.glob("*.json"))
    assert written == ["001.json", "002.json", "003.json"]


def test_recall_empty_memory(employee_root, fake_vessel):
    from company.assets.tools.memento import memento as memento_mod

    with _with_vessel(fake_vessel):
        result = memento_mod.recall.invoke({"query": "anything"})

    assert result["status"] == "ok"
    assert "no prior sessions" in result["context"].lower()
    assert result["session_ids"] == []


def test_recall_after_store_returns_context(employee_root, fake_vessel, monkeypatch):
    """recall returns the patched RecallContext from the adapter."""
    from company.assets.tools.memento import memento as memento_mod
    from onemancompany.core.memory import RecallContext

    class _StoreAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

    class _RecallAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

        async def recall(self, query, conv_id):
            return RecallContext(
                raw_text=f"## Mocked context for '{query}'\n- session_1 hit",
                session_ids=["convE00006_sess1"],
                metadata={"trace": "patched"},
            )

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _StoreAdapter)
    with _with_vessel(fake_vessel):
        memento_mod.store.invoke({"turns": [{"role": "user", "content": "hello"}]})

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _RecallAdapter)
    with _with_vessel(fake_vessel):
        result = memento_mod.recall.invoke({"query": "hi", "top_k": 3})

    assert result["status"] == "ok"
    assert result["query"] == "hi"
    assert "Mocked context" in result["context"]
    assert result["session_ids"] == ["convE00006_sess1"]


def test_recall_top_k_clamps(employee_root, fake_vessel, monkeypatch):
    """top_k below 1 or above 20 is clamped silently."""
    from company.assets.tools.memento import memento as memento_mod

    captured = {}

    class _CapturingAdapter:
        def __init__(self, top_k=5, **_):
            captured["top_k"] = top_k

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

        async def recall(self, query, conv_id):
            from onemancompany.core.memory import RecallContext
            return RecallContext(raw_text="", session_ids=[], metadata={})

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _CapturingAdapter)

    sessions_dir = employee_root / "E00006" / "memory" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "001.json").write_text(
        json.dumps({"session_num": 1, "date_time": "", "turns": [{"role": "user", "content": "x"}]})
    )

    with _with_vessel(fake_vessel):
        # User top_k=0 -> clamped to 1; adapter retrieves at least 10 candidates.
        memento_mod.recall.invoke({"query": "q", "top_k": 0})
        assert captured["top_k"] == 10
        # User top_k=999 -> clamped to 20; adapter still retrieves 20.
        memento_mod.recall.invoke({"query": "q", "top_k": 999})
        assert captured["top_k"] == 20


def test_recall_rejects_empty_query(employee_root, fake_vessel):
    from company.assets.tools.memento import memento as memento_mod

    with _with_vessel(fake_vessel):
        result = memento_mod.recall.invoke({"query": "   "})

    assert result["status"] == "error"
    assert "query" in result["message"].lower()


def test_isolation_two_employees(employee_root, monkeypatch):
    """Sessions stored under E00006 are invisible to E00007."""
    from company.assets.tools.memento import memento as memento_mod
    from onemancompany.core.memory import RecallContext

    (employee_root / "E00007").mkdir(exist_ok=True)

    class _StoreAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

    class _RecallAdapterNothing:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

        async def ingest(self, *_a, **_kw):
            pass

        async def recall(self, *_a, **_kw):
            return RecallContext(raw_text="", session_ids=[], metadata={})

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _StoreAdapter)

    v6 = SimpleNamespace(employee_id="E00006")
    with _with_vessel(v6):
        memento_mod.store.invoke({
            "turns": [{"role": "user", "content": "Acme uses SAML"}]
        })

    e6_sessions = list((employee_root / "E00006" / "memory" / "sessions").glob("*.json"))
    e7_sessions_dir = employee_root / "E00007" / "memory" / "sessions"

    assert len(e6_sessions) == 1
    assert not e7_sessions_dir.exists() or not list(e7_sessions_dir.glob("*.json"))

    monkeypatch.setattr(memento_mod, "MemoryV4Adapter", _RecallAdapterNothing)
    v7 = SimpleNamespace(employee_id="E00007")
    with _with_vessel(v7):
        result = memento_mod.recall.invoke({"query": "Acme"})

    assert result["status"] == "ok"
    assert "no prior sessions" in result["context"].lower()
    assert result["session_ids"] == []


def test_store_finalize_failure_preserves_transcript(employee_root, fake_vessel, monkeypatch):
    """If finalize raises, the session JSON on disk is still written."""
    from company.assets.tools.memento import memento as memento_mod

    class _CrashingAdapter:
        def __init__(self, **_):
            pass

        async def setup(self):
            pass

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
    assert len(written) == 1, "transcript must persist even when finalize crashes"
    payload = json.loads(written[0].read_text())
    assert payload["turns"][0]["content"] == "important fact"
