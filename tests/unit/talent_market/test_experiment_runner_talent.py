"""Structural tests for the bundled Experiment Runner talent.

Validates the talent package layout matches what `load_talent_profile`,
`list_available_talents`, and the hire-time skill-copy path in
`onboarding.py` expect."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


TALENTS_ROOT = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onemancompany"
    / "talent_market"
    / "talents"
)
TALENT_DIR = TALENTS_ROOT / "experiment-runner"


class TestExperimentRunnerProfile:
    def test_directory_exists(self):
        assert TALENT_DIR.exists(), "experiment-runner talent must ship under talents/"

    def test_profile_yaml_valid(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        assert data["id"] == "experiment-runner"
        assert data["name"]
        assert data["role"] == "Engineer"
        assert data["hosting"] == "company"
        assert data["api_provider"] == "openrouter"

    def test_profile_declares_experiment_runner_skill(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        assert "experiment_runner" in data.get("skills", []), (
            "Talent must declare experiment_runner skill so the onboarding "
            "mapping injects the experiment-infra runbook on hire."
        )

    def test_system_prompt_references_experiment_infra_runbook(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        template = data.get("system_prompt_template", "")
        assert "experiment-infra" in template, (
            "System prompt must point the agent at the experiment-infra runbook"
        )
        assert "load_skill" in template, (
            "System prompt should tell the agent how to load the runbook"
        )

    def test_system_prompt_warns_about_session_key_secrecy(self):
        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        template = data.get("system_prompt_template", "")
        assert "INFRA_SESSION_KEY" in template
        assert "secret" in template.lower() or "never" in template.lower()


class TestExperimentRunnerManifest:
    def test_manifest_valid_json(self):
        data = json.loads((TALENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        assert data["id"] == "experiment-runner"
        assert data["hosting"] == "company"

    def test_settings_exposes_infra_server_url_and_session_key(self):
        data = json.loads((TALENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        fields: list[dict] = []
        for section in data.get("settings", {}).get("sections", []):
            fields.extend(section.get("fields", []))
        keys = {f["key"] for f in fields}
        assert "infra_server_url" in keys
        assert "infra_session_key" in keys

    def test_session_key_field_is_secret_type(self):
        data = json.loads((TALENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        for section in data["settings"]["sections"]:
            for f in section["fields"]:
                if f["key"] == "infra_session_key":
                    assert f["type"] == "secret", (
                        "session key must be a secret field so the UI masks it"
                    )
                    return
        assert False, "infra_session_key field not found"


class TestExperimentRunnerSkill:
    """The talent's own skills/experiment_runner/ pointer skill — copied to
    the employee at hire time via the folder-based skill path in
    onboarding._copy_talent_assets."""

    SKILL = TALENT_DIR / "skills" / "experiment_runner" / "SKILL.md"

    def test_skill_md_exists(self):
        assert self.SKILL.exists(), (
            "Folder-based skill at skills/experiment_runner/SKILL.md required "
            "so onboarding.py copies it via shutil.copytree at hire time."
        )

    def test_skill_md_frontmatter(self):
        text = self.SKILL.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        head = text.split("---", 2)[1]
        assert "name: experiment_runner" in head

    def test_skill_md_points_at_experiment_infra_runbook(self):
        text = self.SKILL.read_text(encoding="utf-8")
        assert "experiment-infra" in text
        assert 'load_skill("experiment-infra")' in text


class TestExperimentRunnerWiring:
    """Cross-check between this talent and the onboarding runbook mapping."""

    def test_talent_skill_key_matches_onboarding_mapping(self):
        """The skill key declared in profile.yaml must match the
        `_SKILL_REQUIRED_RUNBOOKS` key, otherwise experiment-infra won't be
        injected when the talent is hired."""
        from onemancompany.agents.onboarding import _SKILL_REQUIRED_RUNBOOKS

        data = yaml.safe_load((TALENT_DIR / "profile.yaml").read_text(encoding="utf-8"))
        for skill_key in data["skills"]:
            if skill_key == "experiment_runner":
                assert skill_key in _SKILL_REQUIRED_RUNBOOKS
                assert "experiment-infra" in _SKILL_REQUIRED_RUNBOOKS[skill_key]
                return
        assert False, "experiment_runner skill key not in profile.yaml"

    def test_list_available_talents_includes_experiment_runner(self):
        from onemancompany.core.config import list_available_talents

        ids = {t["id"] for t in list_available_talents()}
        assert "experiment-runner" in ids, (
            "Talent loader must discover experiment-runner via "
            "list_available_talents() so HR can hire it."
        )

    def test_load_talent_profile_returns_experiment_runner(self):
        from onemancompany.core.config import load_talent_profile

        data = load_talent_profile("experiment-runner")
        assert data, "load_talent_profile must find this talent"
        assert data["id"] == "experiment-runner"
