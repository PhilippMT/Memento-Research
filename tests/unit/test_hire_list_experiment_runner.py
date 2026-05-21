"""Verify the experiment-runner entry is in company/hire_list.json so
start.sh auto-hires it on bootstrap (no manual UI step needed)."""
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
    Stage 6 will fall back to the simulation-only experimentalist."""
    entries = _load()
    matches = [e for e in entries if e.get("talent_id") == "experiment-runner"]
    assert matches, "experiment-runner must be in company/hire_list.json"
    assert len(matches) == 1, "experiment-runner must appear exactly once"


def test_experiment_runner_entry_carries_runner_skill():
    """The skill string is the trigger — onboarding._SKILL_REQUIRED_RUNBOOKS
    keys off `experiment_runner` to inject the experiment-infra +
    execution-runbook skills. If this string drifts, the runbooks won't
    auto-inject and Stage 6 dispatcher will run without its tools."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert "experiment_runner" in entry.get("skills", [])


def test_experiment_runner_entry_uses_local_hosting():
    """The talent lives in the repo (talents/experiment-runner/), not on
    the cloud Talent Market. Hosting must be `company` so hire-from-cv
    resolves it against the local talent dir."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert entry.get("hosting") == "company"


def test_experiment_runner_entry_has_required_cv_fields():
    """hire_from_cv requires `name` and `role`. Without them the auto-hire
    on startup fails with `CV missing required field`."""
    entries = _load()
    entry = next(e for e in entries if e.get("talent_id") == "experiment-runner")
    assert entry.get("name"), "name is required by hire-from-cv"
    assert entry.get("role"), "role is required by hire-from-cv"
