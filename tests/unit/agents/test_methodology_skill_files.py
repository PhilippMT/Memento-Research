"""Sanity tests for the two methodology-related SKILL.md files.

These don't try to validate prose quality — they check that required
sections / keywords exist so that accidental deletion or refactor of the
file is caught immediately."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[3] / "src" / "onemancompany" / "default_skills"
QUALITY_CRITIC = SKILLS_ROOT / "methodology-quality-critic" / "SKILL.md"

# Note: methodology-debate-convener no longer lives in this repo. It is
# hosted as a separate Talent Market repo at
# https://github.com/YihangChen9/methodology-designer (cloned at hire time
# via hire_list.json source_repo). Tests that previously asserted its
# contents were removed alongside the deleted default_skills/ entry — they
# now belong in the talent repo itself.


# ---------------------------------------------------------------------------
# methodology-quality-critic — CCF-A grading
# ---------------------------------------------------------------------------

class TestQualityCriticSkillStructure:
    def test_file_exists(self):
        assert QUALITY_CRITIC.exists()

    def test_lists_eight_grading_dimensions(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        for dim_label in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"):
            assert dim_label in text

    def test_requires_transcript_check(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        assert "transcript" in text.lower()
        assert "stage4_debate_transcript.md" in text
        # Critic must reject if transcript is missing
        assert "REJECT" in text and "debate not run" in text

    def test_specifies_output_format_with_decision(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        assert "Confidence:" in text
        assert "Decision: PASS | REJECT" in text or "Decision: PASS" in text

    def test_decision_rule_requires_d1_through_d5(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        # Decision rule: D1-D5 must PASS for overall PASS
        assert "D1, D2, D3, D4, D5" in text

    def test_mentions_ccf_a_or_icml_neurips_bar(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        assert "CCF-A" in text or "NeurIPS" in text or "ICML" in text


# ---------------------------------------------------------------------------
# D9 — Language & Style enforcement (English default)
# ---------------------------------------------------------------------------

class TestEnglishDefaultAndLanguageDimension:
    def test_critic_has_d9_language_and_style(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        assert "D9" in text, "Critic must include a D9 dimension for language/style"
        assert "Language" in text and "Style" in text

    def test_critic_d9_checks_english(self):
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        # D9 must demand the document be in English
        lower = text.lower()
        assert "english" in lower

    def test_critic_d9_is_not_auto_reject(self):
        """D9 failure should pull confidence but not auto-REJECT, matching
        the existing decision rule pattern for D6/D7/D8."""
        text = QUALITY_CRITIC.read_text(encoding="utf-8")
        # Decision rule line should mention D9 alongside D6/D7/D8 (not in D1-D5)
        assert "D9" in text
        # The decision rule still names D1-D5 as the hard gate
        assert "D1, D2, D3, D4, D5" in text


# ---------------------------------------------------------------------------
# Stage 5 SKILL files — experiment-debate-convener + experiment-quality-critic
# ---------------------------------------------------------------------------

EXP_CONVENER = SKILLS_ROOT / "experiment-debate-convener" / "SKILL.md"
EXP_CRITIC = SKILLS_ROOT / "experiment-quality-critic" / "SKILL.md"


class TestExperimentConvenerSkill:
    def test_file_exists(self):
        assert EXP_CONVENER.exists()

    def test_has_draft_debate_revise_phases(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "Phase 3: Write the Initial Experiment Plan Draft" in text
        assert "Phase 4: Phrase the Critique Topic" in text
        assert "Phase 7: Revise" in text

    def test_has_coordination_phase_producing_assignments(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "Phase 8: Produce the Coordination Assignments Table" in text
        assert "stage5_assignments.md" in text
        assert "分工表" in text or "assignments table" in text.lower()

    def test_requires_english_output(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "English" in text

    def test_lists_all_10_required_sections(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        for section in (
            "Experiment Objective",
            "Variables & Operationalisation",
            "Experimental Procedure",
            "Evaluation Metrics",
            "Sample Size & Power",
            "Pre-registration Spec",
            "Data Pipeline",
            "Failure Modes",
            "Reproducibility",
            "Citation of the Debate",
        ):
            # accept Operationalisation OR Operationalization spelling
            haystack = text.replace("Operationalization", "Operationalisation")
            assert section in haystack, f"missing section heading {section!r}"

    def test_assignments_table_has_required_columns(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        # the example shows the required columns
        for col in ("Task", "Assignee", "Due", "Acceptance criterion"):
            assert col in text

    def test_artifacts_paths_specified(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        for path in (
            "stage5_experiment_v1_draft.md",
            "stage5_experiment_designer.md",
            "stage5_debate_transcript.md",
            "stage5_assignments.md",
        ):
            assert path in text


class TestExperimentCriticSkill:
    def test_file_exists(self):
        assert EXP_CRITIC.exists()

    def test_has_12_dimensions(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        for label in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12"):
            assert label in text

    def test_d10_is_coordination_plan(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        assert "D10" in text and "Coordination Plan" in text

    def test_d12_checks_english(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        assert "D12" in text
        assert "English" in text

    def test_requires_all_three_files(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        for path in (
            "stage5_experiment_designer.md",
            "stage5_assignments.md",
            "stage5_debate_transcript.md",
        ):
            assert path in text

    def test_decision_rule_includes_d10_non_negotiable(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        # D10 must be in the hard-gate list
        assert "D1, D2, D3, D4, D5, D8, D10" in text or "D10" in text
        assert "non-negotiable" in text.lower() or "auto-REJECT" in text

    def test_specifies_output_format_with_decision(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        assert "Confidence:" in text
        assert "Decision: PASS" in text
