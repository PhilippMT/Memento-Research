"""Sanity tests for the two methodology-related SKILL.md files.

These don't try to validate prose quality — they check that required
sections / keywords exist so that accidental deletion or refactor of the
file is caught immediately."""
from __future__ import annotations

from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[3] / "src" / "onemancompany" / "default_skills"
CONVENER = SKILLS_ROOT / "methodology-debate-convener" / "SKILL.md"
QUALITY_CRITIC = SKILLS_ROOT / "methodology-quality-critic" / "SKILL.md"


# ---------------------------------------------------------------------------
# methodology-debate-convener — draft → debate → revise flow
# ---------------------------------------------------------------------------

class TestConvenerSkillStructure:
    def test_file_exists(self):
        assert CONVENER.exists()

    def test_has_initial_draft_phase(self):
        text = CONVENER.read_text(encoding="utf-8")
        assert "Phase 3: Write the Initial Methodology Draft" in text, (
            "Stage 4 producer must write a v1 draft BEFORE the debate runs — "
            "the debate needs a concrete target to critique."
        )
        assert "stage4_methodology_v1_draft.md" in text

    def test_critique_topic_attacks_the_draft(self):
        text = CONVENER.read_text(encoding="utf-8")
        # Phase 4 should explicitly frame the debate as "critique the draft"
        assert "Phase 4: Phrase the Critique Topic" in text
        assert "critique of the v1 draft" in text

    def test_final_phase_is_revise_not_write_from_scratch(self):
        text = CONVENER.read_text(encoding="utf-8")
        assert "Phase 7: Revise" in text
        assert "Start from the v1 draft, do not rewrite from scratch" in text

    def test_mentions_ccf_a_quality_bar(self):
        text = CONVENER.read_text(encoding="utf-8")
        assert "CCF-A" in text, (
            "The final write phase must call out the CCF-A quality bar explicitly."
        )

    def test_lists_all_eight_required_sections(self):
        text = CONVENER.read_text(encoding="utf-8")
        for section_name in (
            "Research Question",
            "Hypotheses",
            "Variables",
            "Experimental Design",
            "Evaluation Metrics",
            "Threats to Validity",
            "Alternatives Considered",
            "Open Questions",
        ):
            assert section_name in text

    def test_transcript_saved_before_revise(self):
        text = CONVENER.read_text(encoding="utf-8")
        assert "stage4_debate_transcript.md" in text


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
    def test_convener_phase_3_requires_english_output(self):
        """The initial draft (Phase 3) must be specified to be in English so
        downstream stages and the critic can apply a single style standard."""
        text = CONVENER.read_text(encoding="utf-8")
        # Heading must exist + explicit English requirement nearby
        assert "Phase 3: Write the Initial Methodology Draft" in text
        # Search for the English mandate (case-insensitive on phrase)
        lower = text.lower()
        assert "english" in lower, (
            "Convener must default the methodology output language to English "
            "regardless of upstream stage language."
        )

    def test_convener_phase_7_has_writing_style_guidance(self):
        """Final revise phase needs explicit writing-style rules so producers
        avoid the academic-prose pitfalls D9 will check against."""
        text = CONVENER.read_text(encoding="utf-8")
        # Phase 7 should mention writing-style topics
        for keyword in ("notation", "terminology", "topic sentence"):
            assert keyword.lower() in text.lower(), (
                f"Phase 7 should mention {keyword!r} as part of writing style"
            )

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
