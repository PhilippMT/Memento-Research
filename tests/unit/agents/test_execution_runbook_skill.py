"""Structural tests for the Stage 6 experiment-execution-runbook SKILL.

The runbook is the bridge between Stage 5's assignments table and the
experiment-infra API — it must reference the assignments file, describe the
per-row routing, and tell the agent to never fabricate run_ids."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "default_skills"
)
RUNBOOK = SKILLS_ROOT / "experiment-execution-runbook" / "SKILL.md"


class TestRunbookExists:
    def test_skill_folder_exists(self):
        assert RUNBOOK.parent.exists()

    def test_skill_md_exists(self):
        assert RUNBOOK.exists()

    def test_frontmatter_present(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: experiment-execution-runbook" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_allowed_tools_include_bash(self):
        """The runbook executes fast_*.sh scripts — Bash must be allowed."""
        text = RUNBOOK.read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        assert "Bash" in head


class TestRunbookBehaviour:
    """The prose contract — these assertions catch silent drift if someone
    refactors the SKILL.md and loses the load-bearing instructions."""

    def test_reads_assignments_table(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stage5_assignments.md" in text, (
            "Stage 6 runbook must explicitly name the assignments table "
            "(the bridge from Stage 5 to Stage 6)"
        )

    def test_routes_by_skill_column(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "experiment_runner" in text, (
            "Runbook must mention the skill key it routes on"
        )

    def test_loads_experiment_infra_for_remote_rows(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert 'load_skill("experiment-infra")' in text, (
            "For experiment_runner rows the runbook must instruct the "
            "agent to load the experiment-infra runbook"
        )

    def test_names_the_fast_scripts(self):
        """If the script names change in experiment-infra, this test fails so
        we update both in lock-step."""
        text = RUNBOOK.read_text(encoding="utf-8")
        for script in (
            "fast_query_budget.sh",
            "fast_submit.sh",
            "fast_query_exp_status.sh",
        ):
            assert script in text, f"Runbook must name {script}"

    def test_writes_stage6_output(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stage6_experimentalist.md" in text, (
            "Stage 6 runbook must write to stage6_experimentalist.md so "
            "the pipeline engine's filename convention is honored"
        )

    def test_records_run_id_immediately(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "run_id" in text
        # Must explicitly call out recording the run_id at submit time
        assert "Record the run_id" in text or "record the run_id" in text

    def test_forbids_fabrication(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        # The Stage 6 critic specifically auto-REJECTs fabricated results,
        # so the producer-side runbook must mirror that contract.
        assert "fabricat" in text.lower() or "Don't fabricat" in text

    def test_forbids_session_key_echo(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "INFRA_SESSION_KEY" in text

    def test_documents_degraded_mode(self):
        """When no experiment_runner is on roster, the dispatcher must
        not simulate — it should surface the gap so the CEO can hire one."""
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "Degraded mode" in text or "degraded" in text.lower()


class TestRunbookOnboardingWiring:
    """Cross-check that the runbook is wired into onboarding for the
    right skill keys."""

    def test_listed_in_skill_required_runbooks_for_experiment_runner(self):
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS
        runbooks = _SKILL_REQUIRED_RUNBOOKS.get("experiment_runner", [])
        assert "experiment-execution-runbook" in runbooks
