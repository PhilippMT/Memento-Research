"""Structural tests for the Stage 7 result-analysis-runbook SKILL.

The runbook is the bridge between Stage 4/5's pre-registration contract,
Stage 6's actual evidence, and the Stage 7 confirmatory analysis. It must
read all four prior artifacts, write a stage7_result_analyst.md report,
forbid HARKing, refuse metric substitution, require effect sizes (not
bare p-values), separate confirmatory from exploratory, and cap the
verdict to Stage 6's coverage."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "default_skills"
)
RUNBOOK = SKILLS_ROOT / "result-analysis-runbook" / "SKILL.md"


class TestRunbookExists:
    def test_skill_folder_exists(self):
        assert RUNBOOK.parent.exists()

    def test_skill_md_exists(self):
        assert RUNBOOK.exists()

    def test_frontmatter_present(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: result-analysis-runbook" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_allowed_tools_include_read_and_write(self):
        """Stage 7 is pure analysis — Read (to pull prior artifacts) and
        Write (to produce stage7_result_analyst.md) are sufficient. No
        Bash required."""
        text = RUNBOOK.read_text(encoding="utf-8")
        head = text.split("---", 2)[1]
        assert "Read" in head
        assert "Write" in head


class TestRunbookBehaviour:
    """The prose contract — these assertions catch silent drift if someone
    refactors the SKILL.md and loses the load-bearing instructions."""

    def test_reads_all_four_prior_artifacts(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for artifact in (
            "stage4_methodology_designer.md",
            "stage5_experiment_designer.md",
            "stage5_assignments.md",
            "stage6_experimentalist.md",
        ):
            assert artifact in text, (
                f"Stage 7 runbook must explicitly name {artifact} — it is "
                f"a load-bearing input to the pre-registration contract"
            )

    def test_writes_stage7_output(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "stage7_result_analyst.md" in text, (
            "Stage 7 runbook must write to stage7_result_analyst.md so "
            "the pipeline engine's filename convention is honored"
        )

    def test_forbids_harking(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "HARK" in text.upper(), (
            "Stage 7 runbook must explicitly forbid HARKing (Hypothesising "
            "After Results are Known)"
        )

    def test_forbids_metric_substitution(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "NOT TESTED" in text or "do not substitute" in text.lower(), (
            "Stage 7 runbook must instruct the analyst to declare NOT "
            "TESTED rather than silently swap in a similar metric"
        )

    def test_requires_effect_sizes_not_just_p_values(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        assert (
            "effect size" in lowered
            or "confidence interval" in lowered
            or "95% ci" in lowered
        ), (
            "Stage 7 runbook must require effect sizes / CIs, not bare "
            "p-values"
        )

    def test_separates_confirmatory_and_exploratory(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "confirmatory" in lowered, (
            "Stage 7 runbook must define confirmatory analysis"
        )
        assert "exploratory" in lowered, (
            "Stage 7 runbook must separate exploratory observations from "
            "confirmatory results"
        )

    def test_caps_verdict_to_stage6_coverage(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        assert (
            "coverage" in text.lower()
            or "BLOCKED" in text
            or "INCONCLUSIVE_DUE_TO_COVERAGE" in text
        ), (
            "Stage 7 runbook must cap the overall verdict to Stage 6's "
            "actual coverage — no upgrades beyond evidence"
        )

    def test_documents_degraded_mode(self):
        """When Stage 6 was BLOCKED entirely, the analyst must surface
        the gap with INCONCLUSIVE_DUE_TO_COVERAGE rather than fabricate
        a confirmatory result."""
        text = RUNBOOK.read_text(encoding="utf-8")
        assert "Degraded mode" in text or "degraded" in text.lower()


class TestRunbookOnboardingWiring:
    """Cross-check that the runbook is wired into onboarding for the
    right skill keys."""

    def test_listed_in_skill_required_runbooks_for_result_analyst(self):
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS
        runbooks = _SKILL_REQUIRED_RUNBOOKS.get("result_analyst", [])
        assert "result-analysis-runbook" in runbooks
