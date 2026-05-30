"""``_bootstrap_hire_list_employees`` ensures the founding roster is hired
during FastAPI lifespan startup, BEFORE uvicorn binds the port. Replaces
the fragile ``start.sh`` HTTP loop that was prone to mid-flight backend
crashes and silent ✗ failures."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _cv(talent_id: str, name: str = "") -> dict:
    return {
        "name": name or talent_id, "role": "Researcher",
        "talent_id": talent_id, "hosting": "company",
        "skills": [talent_id.replace("-", "_")], "tools": [],
        "system_prompt_template": f"You are {talent_id}.",
        "source_type": "talent_market",
    }


@pytest.fixture
def hire_file(tmp_path: Path, monkeypatch) -> Path:
    """Point DATA_ROOT/company/hire_list.json at a temp file the test owns."""
    company_dir = tmp_path / "company"
    company_dir.mkdir(parents=True)
    target = company_dir / "hire_list.json"
    from onemancompany import main
    monkeypatch.setattr(main, "DATA_ROOT", tmp_path, raising=False)
    return target


def test_bootstrap_no_op_when_hire_list_missing(tmp_path, monkeypatch):
    from onemancompany import main
    monkeypatch.setattr(main, "DATA_ROOT", tmp_path, raising=False)
    # No company/ dir at all → should return cleanly.
    asyncio.run(main._bootstrap_hire_list_employees())


def test_bootstrap_invokes_hire_for_each_pending_cv(hire_file, monkeypatch):
    hire_file.write_text(json.dumps([
        _cv("topic-refiner"), _cv("idea-generator"),
    ]), encoding="utf-8")

    # No existing employees on the roster — both CVs are pending.
    monkeypatch.setattr(
        "onemancompany.main.load_employee_configs", lambda: {}, raising=False,
    )

    calls = []
    hire_mock = AsyncMock(return_value={"status": "hired"})
    async def _wrapper(body):
        calls.append(body)
        return await hire_mock(body)

    with patch("onemancompany.api.routes.hire_from_cv", _wrapper):
        from onemancompany import main
        asyncio.run(main._bootstrap_hire_list_employees())

    talent_ids = [c["cv"]["talent_id"] for c in calls]
    assert talent_ids == ["topic-refiner", "idea-generator"]
    assert all(c["sync"] is True for c in calls), \
        "lifespan must use sync mode so it actually waits for the hire to land"


def test_bootstrap_skips_talents_already_on_roster(hire_file, monkeypatch):
    hire_file.write_text(json.dumps([
        _cv("topic-refiner"), _cv("idea-generator"),
    ]), encoding="utf-8")

    # topic-refiner already hired (under emp number 00007) — only
    # idea-generator should be dispatched.
    existing = {
        "00007": SimpleNamespace(talent_id="topic-refiner", name="Topic Refiner"),
    }
    monkeypatch.setattr(
        "onemancompany.main.load_employee_configs", lambda: existing, raising=False,
    )

    calls = []
    async def _wrapper(body):
        calls.append(body)
        return {"status": "hired"}

    with patch("onemancompany.api.routes.hire_from_cv", _wrapper):
        from onemancompany import main
        asyncio.run(main._bootstrap_hire_list_employees())

    assert [c["cv"]["talent_id"] for c in calls] == ["idea-generator"]


def test_bootstrap_continues_when_one_hire_raises(hire_file, monkeypatch):
    """A single failing hire must NOT abort the rest of the roster bootstrap.
    Regression for the reported case where Methodology Designer crashed and
    every subsequent CV got Connection refused."""
    hire_file.write_text(json.dumps([
        _cv("topic-refiner"),
        _cv("methodology-designer"),
        _cv("idea-generator"),
    ]), encoding="utf-8")

    monkeypatch.setattr(
        "onemancompany.main.load_employee_configs", lambda: {}, raising=False,
    )

    seen = []
    async def _wrapper(body):
        tid = body["cv"]["talent_id"]
        seen.append(tid)
        if tid == "methodology-designer":
            raise RuntimeError("simulated crash mid-hire")
        return {"status": "hired"}

    with patch("onemancompany.api.routes.hire_from_cv", _wrapper):
        from onemancompany import main
        asyncio.run(main._bootstrap_hire_list_employees())

    # All three were attempted in order; the crash on #2 didn't abort #3.
    assert seen == ["topic-refiner", "methodology-designer", "idea-generator"]


def test_lifespan_calls_bootstrap_before_agent_registration_loop():
    """Source-level invariant: ``_bootstrap_hire_list_employees`` must run
    BEFORE the agent registration loop (``for emp_id, emp_data in
    _store_mod.load_all_employees()``). Otherwise new hires land on disk
    but never become serving agents, and pipeline stages silently fall
    back to ``auto`` — the original regression this PR fixed.

    Locks the ordering into the test suite so a future lifespan reshuffle
    doesn't silently re-break it."""
    import inspect
    from onemancompany import main as _main

    src = inspect.getsource(_main.lifespan)
    bootstrap_idx = src.find("await _bootstrap_hire_list_employees()")
    reg_loop_idx = src.find("for emp_id, emp_data in _store_mod.load_all_employees()")
    assert bootstrap_idx > 0, "bootstrap call missing from lifespan"
    assert reg_loop_idx > 0, "agent-registration loop missing from lifespan"
    assert bootstrap_idx < reg_loop_idx, (
        "_bootstrap_hire_list_employees() must run BEFORE the agent "
        "registration loop, otherwise newly-hired employees won't be "
        "registered as serving agents and stages fall back to 'auto'"
    )
