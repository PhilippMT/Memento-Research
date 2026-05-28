"""Verify the Stage-6 entries are in company/hire_list.json so start.sh
auto-hires both on bootstrap (no manual UI step needed). Both talents
now live on the Talent Market multi-talent repo at
https://github.com/YihangChen9/experiment-team — `talent_market.onboard()`
resolves their repo URL at hire time.
"""
from __future__ import annotations

import json
from pathlib import Path


HIRE_LIST = Path(__file__).resolve().parents[2] / "company" / "hire_list.json"


def _load() -> list[dict]:
    return json.loads(HIRE_LIST.read_text(encoding="utf-8"))


def test_hire_list_file_exists():
    assert HIRE_LIST.exists()


def test_experiment_runner_in_hire_list():
    """Without this entry the runner won't auto-hire on `start.sh`, and
    Stage 6's execution sub-phase has no employee to dispatch to."""
    entries = _load()
    matches = [e for e in entries if e.get("talent_id") == "experiment-runner"]
    assert matches, "experiment-runner must be in company/hire_list.json"
    assert len(matches) == 1, "experiment-runner must appear exactly once"


def test_experiment_runner_entry_carries_runner_skill():
    """The skill string is the dispatch key — pipeline_engine looks for
    employees with `experiment_runner` for the Stage 6 execution
    sub-phase. If this string drifts, dispatch breaks."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert "experiment_runner" in entry.get("skills", [])


def test_experiment_runner_uses_talent_market():
    """Source_type must be `talent_market` so `_do_cv_hire` calls
    `talent_market.onboard()` to resolve the repo URL. The talent now
    lives at https://github.com/YihangChen9/experiment-team (multi-talent
    repo with experiment-code-writer + experiment-runner)."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert entry.get("source_type") == "talent_market", (
        "experiment-runner must be sourced from Talent Market, not the "
        "deleted local built-in talent"
    )


def test_experiment_runner_entry_has_required_cv_fields():
    """hire_from_cv requires `name` and `role`. Without them the auto-hire
    on startup fails with `CV missing required field`."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert entry.get("name"), "name is required by hire-from-cv"
    assert entry.get("role"), "role is required by hire-from-cv"


def test_experiment_code_writer_in_hire_list():
    """Stage 6's implementation sub-phase routes to a `code_implementer`
    employee. Without this entry the impl producer fails with `No
    employee with skill 'code_implementer' for stage 6`."""
    entries = _load()
    matches = [e for e in entries if e.get("talent_id") == "experiment-code-writer"]
    assert matches, "experiment-code-writer must be in company/hire_list.json"


def test_experiment_code_writer_carries_code_implementer_skill():
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-code-writer")
    assert "code_implementer" in entry.get("skills", [])


def test_experiment_code_writer_uses_talent_market():
    """Source_type must be `talent_market` so onboard() resolves the repo
    URL (also https://github.com/YihangChen9/experiment-team)."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-code-writer")
    assert entry.get("source_type") == "talent_market"
