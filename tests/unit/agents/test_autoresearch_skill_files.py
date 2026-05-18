"""Sanity tests for the bundled `autoresearch` default skill.

Validates that the skill folder ships with the expected file layout,
frontmatter, scripts, and that no real credentials leaked into the
repo. Also covers the Stage 5 cross-references that wire this skill
into the experiment-debate convener and quality critic."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[3] / "src" / "onemancompany" / "default_skills"
AUTORESEARCH = SKILLS_ROOT / "autoresearch"
EXP_CONVENER = SKILLS_ROOT / "experiment-debate-convener" / "SKILL.md"
EXP_CRITIC = SKILLS_ROOT / "experiment-quality-critic" / "SKILL.md"


class TestAutoresearchLayout:
    def test_skill_folder_exists(self):
        assert AUTORESEARCH.exists(), "autoresearch skill folder must ship under default_skills/"

    def test_skill_md_frontmatter(self):
        text = (AUTORESEARCH / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: autoresearch" in head
        assert "description:" in head
        assert "allowed-tools:" in head

    def test_all_eight_scripts_present(self):
        expected = {
            "fast_query_budget.sh",
            "fast_query_server_info.sh",
            "fast_query_working_dir.sh",
            "fast_push_code.sh",
            "fast_submit.sh",
            "fast_query_exp_status.sh",
            "fast_cancel.sh",
            "fast_cancel_all_running.sh",
        }
        present = {p.name for p in (AUTORESEARCH / "scripts").iterdir() if p.is_file()}
        missing = expected - present
        assert not missing, f"Missing autoresearch scripts: {missing}"

    def test_scripts_are_executable(self):
        for script in (AUTORESEARCH / "scripts").glob("*.sh"):
            mode = script.stat().st_mode
            assert mode & stat.S_IXUSR, f"{script.name} is missing the user-exec bit"

    def test_references_present(self):
        assert (AUTORESEARCH / "references" / "exp-configuration.md").exists()
        assert (AUTORESEARCH / "references" / "runtime_images.json").exists()

    def test_runtime_images_is_valid_json(self):
        data = json.loads(
            (AUTORESEARCH / "references" / "runtime_images.json").read_text(encoding="utf-8")
        )
        assert isinstance(data, dict)
        assert "images" in data or "default_image" in data, (
            "runtime_images.json should list available SkyPilot runtime images"
        )

    def test_qwen_walkthrough_present(self):
        assert (AUTORESEARCH / "receipt" / "qwen_inference.md").exists()

    def test_no_pycache_or_dsstore_leaked(self):
        for root, dirs, files in os.walk(AUTORESEARCH):
            assert "__pycache__" not in dirs, f"__pycache__ leaked under {root}"
            assert ".DS_Store" not in files, f".DS_Store leaked under {root}"


class TestAutoresearchCredentialSafety:
    """Real session keys must never enter the repo; only the .example file ships."""

    def test_example_credentials_shipped(self):
        ex = AUTORESEARCH / "autoresearch_credentials.example.json"
        assert ex.exists(), "example credentials file must ship so users know the schema"
        data = json.loads(ex.read_text(encoding="utf-8"))
        assert set(data.keys()) == {"server_url", "session_key"}

    def test_example_credentials_are_placeholders(self):
        data = json.loads(
            (AUTORESEARCH / "autoresearch_credentials.example.json").read_text(encoding="utf-8")
        )
        # Placeholder values — never a real session key like ``vk_<name>_<hex>``.
        assert "YOUR" in data["server_url"] or "EXAMPLE" in data["server_url"].upper()
        assert "REPLACE" in data["session_key"].upper() or "EXAMPLE" in data["session_key"].upper()

    def test_real_credentials_file_absent(self):
        real = AUTORESEARCH / "autoresearch_credentials.json"
        assert not real.exists(), (
            "autoresearch_credentials.json must stay gitignored — real keys leaked into repo"
        )

    def test_gitignore_covers_real_credentials(self):
        repo_root = Path(__file__).resolve().parents[3]
        gi = (repo_root / ".gitignore").read_text(encoding="utf-8")
        assert "autoresearch/autoresearch_credentials.json" in gi, (
            ".gitignore must list the autoresearch real-credentials file"
        )


class TestStage5WiringToAutoresearch:
    """Stage 5 SKILL.md files must mention the runner/autoresearch path
    so the producer + critic know remote-execution tasks need a runner."""

    def test_convener_mentions_experiment_runner_in_team_assembly(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "experiment_runner" in text
        assert "autoresearch" in text

    def test_convener_coordination_rules_require_runner_for_remote_tasks(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "fast_submit.sh" in text, (
            "Coordination rules should name the autoresearch script so Stage 6 "
            "knows the assignment format for remote-execution tasks"
        )

    def test_critic_d10_checks_runner_assignment(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        # D10 gains a sub-check that remote-execution tasks have an
        # experiment_runner-skilled assignee.
        assert "experiment_runner" in text
