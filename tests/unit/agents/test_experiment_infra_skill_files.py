"""Sanity tests for the bundled `experiment-infra` default skill.

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
EXPERIMENT_INFRA = SKILLS_ROOT / "experiment-infra"
EXP_CONVENER = SKILLS_ROOT / "experiment-debate-convener" / "SKILL.md"
EXP_CRITIC = SKILLS_ROOT / "experiment-quality-critic" / "SKILL.md"


class TestExperimentInfraLayout:
    def test_skill_folder_exists(self):
        assert EXPERIMENT_INFRA.exists(), "experiment-infra skill folder must ship under default_skills/"

    def test_skill_md_frontmatter(self):
        text = (EXPERIMENT_INFRA / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        head = text.split("---", 2)[1]
        assert "name: experiment-infra" in head
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
        present = {p.name for p in (EXPERIMENT_INFRA / "scripts").iterdir() if p.is_file()}
        missing = expected - present
        assert not missing, f"Missing experiment-infra scripts: {missing}"

    def test_scripts_are_executable(self):
        for script in (EXPERIMENT_INFRA / "scripts").glob("*.sh"):
            mode = script.stat().st_mode
            assert mode & stat.S_IXUSR, f"{script.name} is missing the user-exec bit"

    def test_references_present(self):
        assert (EXPERIMENT_INFRA / "references" / "exp-configuration.md").exists()
        assert (EXPERIMENT_INFRA / "references" / "runtime_images.json").exists()

    def test_runtime_images_is_valid_json(self):
        data = json.loads(
            (EXPERIMENT_INFRA / "references" / "runtime_images.json").read_text(encoding="utf-8")
        )
        assert isinstance(data, dict)
        assert "images" in data or "default_image" in data, (
            "runtime_images.json should list available SkyPilot runtime images"
        )

    def test_qwen_walkthrough_present(self):
        assert (EXPERIMENT_INFRA / "receipt" / "qwen_inference.md").exists()

    def test_no_pycache_or_dsstore_leaked(self):
        for root, dirs, files in os.walk(EXPERIMENT_INFRA):
            assert "__pycache__" not in dirs, f"__pycache__ leaked under {root}"
            assert ".DS_Store" not in files, f".DS_Store leaked under {root}"


class TestExperimentInfraCredentialSafety:
    """Real session keys must never enter the repo; only the .example file ships."""

    def test_example_credentials_shipped(self):
        ex = EXPERIMENT_INFRA / "experiment_infra_credentials.example.json"
        assert ex.exists(), "example credentials file must ship so users know the schema"
        data = json.loads(ex.read_text(encoding="utf-8"))
        assert set(data.keys()) == {"server_url", "session_key"}

    def test_example_credentials_are_placeholders(self):
        data = json.loads(
            (EXPERIMENT_INFRA / "experiment_infra_credentials.example.json").read_text(encoding="utf-8")
        )
        # Placeholder values — never a real session key like ``vk_<name>_<hex>``.
        assert "YOUR" in data["server_url"] or "EXAMPLE" in data["server_url"].upper()
        assert "REPLACE" in data["session_key"].upper() or "EXAMPLE" in data["session_key"].upper()

    def test_real_credentials_file_not_tracked_by_git(self):
        """The real credentials file may be dropped locally for runtime
        smoke tests, but it must never be tracked by git. We assert the
        gitignore + `git ls-files` state, not the filesystem state —
        otherwise dev workflows that need a local credentials file would
        falsely fail this test."""
        import subprocess

        real = EXPERIMENT_INFRA / "experiment_infra_credentials.json"
        repo_root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--error-unmatch",
                str(real.relative_to(repo_root)),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        # ls-files --error-unmatch exits non-zero if the path is not tracked.
        assert result.returncode != 0, (
            "experiment_infra_credentials.json is tracked by git — real keys "
            "must never enter the repo. Remove with `git rm --cached`."
        )

    def test_gitignore_covers_real_credentials(self):
        repo_root = Path(__file__).resolve().parents[3]
        gi = (repo_root / ".gitignore").read_text(encoding="utf-8")
        assert "experiment-infra/experiment_infra_credentials.json" in gi, (
            ".gitignore must list the experiment-infra real-credentials file"
        )


class TestStage5WiringToExperimentInfra:
    """Stage 5 SKILL.md files must mention the runner / experiment-infra path
    so the producer + critic know remote-execution tasks need a runner."""

    def test_convener_mentions_experiment_runner_in_team_assembly(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "experiment_runner" in text
        assert "experiment-infra" in text

    def test_convener_coordination_rules_require_runner_for_remote_tasks(self):
        text = EXP_CONVENER.read_text(encoding="utf-8")
        assert "fast_submit.sh" in text, (
            "Coordination rules should name the experiment-infra script so Stage 6 "
            "knows the assignment format for remote-execution tasks"
        )

    def test_critic_d10_checks_runner_assignment(self):
        text = EXP_CRITIC.read_text(encoding="utf-8")
        # D10 gains a sub-check that remote-execution tasks have an
        # experiment_runner-skilled assignee.
        assert "experiment_runner" in text
