"""Integration tests - hit a real LLM.

Skipped automatically when OPENROUTER_API_KEY is not set so unit-test runs
stay fast and offline. Each test pulls 1-3 sessions from the shared
corpus fixture, stores them via the actual store() tool, then asserts
on the resulting causal graph / supersede sidecar / recall results.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from onemancompany.core.vessel import _current_vessel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "memento_e2e_corpus.yaml"


_HAS_LLM = bool(os.environ.get("OPENROUTER_API_KEY"))
pytestmark = pytest.mark.skipif(
    not _HAS_LLM,
    reason="OPENROUTER_API_KEY not set - skipping real-LLM integration tests",
)


@pytest.fixture
def corpus():
    return yaml.safe_load(CORPUS_PATH.read_text())


@pytest.fixture
def isolated_employees(tmp_path, monkeypatch):
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    monkeypatch.setattr(
        "onemancompany.core.config.EMPLOYEES_DIR", employees_dir, raising=False
    )
    import company.assets.tools.memento.memento as memento_mod
    monkeypatch.setattr(memento_mod, "EMPLOYEES_DIR", employees_dir, raising=False)
    return employees_dir


@contextmanager
def _vessel(employee_id: str):
    token = _current_vessel.set(SimpleNamespace(employee_id=employee_id))
    try:
        yield
    finally:
        _current_vessel.reset(token)


def test_real_llm_finalize_preserves_verbatim_quotes(isolated_employees, corpus):
    """Store the orders-api constants session, expect 8745 to land in context."""
    from company.assets.tools.memento.memento import store, recall
    sess = next(s for s in corpus["sessions"] if s["num"] == 13)

    (isolated_employees / "EMP-VERBATIM").mkdir()
    with _vessel("EMP-VERBATIM"):
        store_result = store.invoke({"turns": sess["turns"]})
        assert store_result["status"] == "ok", store_result

        recall_result = recall.invoke({"query": "What port does orders-api use in production?"})

    assert recall_result["status"] == "ok"
    assert "8745" in recall_result["context"], (
        "verbatim port number must appear in recall context"
    )


def test_supersede_chain_promotes_latest_decision(isolated_employees, corpus):
    """Store sessions 9, 10, 11 (PG -> MySQL -> PG). Recall ranks the latest top."""
    from company.assets.tools.memento.memento import store, recall

    (isolated_employees / "EMP-SUPER").mkdir()
    sessions = [s for s in corpus["sessions"] if s["num"] in (9, 10, 11)]

    with _vessel("EMP-SUPER"):
        for s in sessions:
            r = store.invoke({"turns": s["turns"]})
            assert r["status"] == "ok"
        recall_result = recall.invoke({
            "query": "What is our current primary database for the analytics service?",
            "top_k": 5,
        })

    assert recall_result["status"] == "ok"
    sess_nums = []
    for sid in recall_result.get("session_ids", []):
        if "_sess" in sid:
            tail = sid.rsplit("_sess", 1)[1]
            if tail.isdigit():
                sess_nums.append(int(tail))
    assert sess_nums, "recall must surface at least one session"
    # Internal session_num order: 1 (PG), 2 (MySQL), 3 (PG revert)
    assert sess_nums[0] == 3, (
        f"expected the latest stored session (internal num 3) to be top1, got {sess_nums}"
    )


def test_two_employees_isolated_real_llm(isolated_employees, corpus):
    """Each employee's store + recall stays in its own directory; recall does not cross."""
    from company.assets.tools.memento.memento import store, recall

    (isolated_employees / "EMP-A").mkdir()
    (isolated_employees / "EMP-B").mkdir()

    sess_a = next(s for s in corpus["sessions"] if s["num"] == 1)
    sess_b = next(s for s in corpus["sessions"] if s["num"] == 4)

    with _vessel("EMP-A"):
        store.invoke({"turns": sess_a["turns"]})
    with _vessel("EMP-B"):
        store.invoke({"turns": sess_b["turns"]})

    with _vessel("EMP-A"):
        a_recall = recall.invoke({"query": "frontend hover state"})
    with _vessel("EMP-B"):
        b_recall = recall.invoke({"query": "Acme SAML SSO"})

    assert "iOS Safari" not in a_recall.get("context", "")
    assert "sso.acme.example" not in b_recall.get("context", "")

    a_sess_files = list((isolated_employees / "EMP-A" / "memory" / "sessions").glob("*.json"))
    b_sess_files = list((isolated_employees / "EMP-B" / "memory" / "sessions").glob("*.json"))
    assert len(a_sess_files) == 1
    assert len(b_sess_files) == 1
    assert json.loads(a_sess_files[0].read_text())["turns"][0]["content"].lower().startswith(
        "onboarding doc for the acme account"
    )
