"""Tests for assemble_specialist_from_skill — hire AI-generated specialists
backed by SkillsMP cloud skills."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# assemble_specialist_from_skill — @tool wrapper
# ---------------------------------------------------------------------------

class TestAssembleSpecialistTool:
    """Black-box tests for the LLM-facing @tool. All side-effecting deps mocked."""

    def _patch_deps(self, monkeypatch, *, api_key="sk_live_test", emp_id="00099",
                    install_ok=True, install_text="installed: experiment-design"):
        from onemancompany.core import config as cfg
        from onemancompany.agents import common_tools, onboarding

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", api_key, raising=False)
        monkeypatch.setattr(cfg.settings, "default_api_provider", "openrouter", raising=False)

        async def fake_generate_nickname(name, role, is_founding=False):
            return name.split()[0][:8]

        async def fake_execute_hire(**kwargs):
            return SimpleNamespace(id=emp_id, name=kwargs.get("name"))

        async def fake_install(employee_id, skill_github_url, api_key=""):
            if not install_ok:
                raise RuntimeError("network down")
            return install_text

        monkeypatch.setattr(onboarding, "generate_nickname", fake_generate_nickname)
        monkeypatch.setattr(onboarding, "execute_hire", fake_execute_hire)
        monkeypatch.setattr(onboarding, "_install_cloud_skill_for_employee", fake_install)

        return common_tools

    def test_happy_path_returns_employee_id_and_skill(self, monkeypatch):
        common_tools = self._patch_deps(monkeypatch)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr Alex Causal",
            "role": "Causal Inference Statistician",
            "skill_github_url": "https://github.com/foo/repo/tree/main/skills/experiment-design",
        }))

        assert out["status"] == "ok"
        assert out["employee_id"] == "00099"
        assert out["name"] == "Dr Alex Causal"
        assert out["installed_skill"] == "experiment-design"
        assert "installed:" in out.get("install_result", "")

    def test_missing_skillsmp_key_returns_error(self, monkeypatch):
        common_tools = self._patch_deps(monkeypatch, api_key="")

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "X", "role": "Y",
            "skill_github_url": "https://github.com/a/b/tree/main/c",
        }))

        assert out.get("is_error") is True
        assert "SKILLSMP_API_KEY" in out["message"]

    def test_non_github_url_returns_error(self, monkeypatch):
        common_tools = self._patch_deps(monkeypatch)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "X", "role": "Y",
            "skill_github_url": "https://skillsmp.com/skills/foo",  # not a github tree URL
        }))

        assert out.get("is_error") is True
        assert "github.com" in out["message"].lower()

    def test_skill_install_failure_returns_partial_with_emp_id(self, monkeypatch):
        common_tools = self._patch_deps(monkeypatch, install_ok=False)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr Test", "role": "Tester",
            "skill_github_url": "https://github.com/x/y/tree/main/z",
        }))

        assert out["status"] == "ok_partial"
        assert out["employee_id"] == "00099"
        assert "skill install failed" in out["message"]


# ---------------------------------------------------------------------------
# _install_cloud_skill_for_employee — helper that spawns fastskills MCP
# ---------------------------------------------------------------------------

class TestInstallCloudSkillForEmployee:
    def test_missing_api_key_raises(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import config as cfg

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "", raising=False)

        with pytest.raises(RuntimeError, match="SKILLSMP_API_KEY"):
            asyncio.run(onboarding._install_cloud_skill_for_employee(
                "00099",
                "https://github.com/x/y/tree/main/z",
            ))

    def test_uses_employee_skills_dir(self, monkeypatch, tmp_path):
        """Validates that the function targets the employee's own skills_dir.
        Mocks the MCP stdio transport so we don't actually spawn fastskills."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as cfg

        # Point EMPLOYEES_DIR at tmp_path for isolation
        monkeypatch.setattr(onboarding, "EMPLOYEES_DIR", tmp_path, raising=False)
        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "sk_live_test", raising=False)

        captured = {}

        class FakeSession:
            async def initialize(self):
                pass
            async def call_tool(self, name, args):
                captured["tool"] = name
                captured["args"] = args
                return SimpleNamespace(content=[SimpleNamespace(text="ok install: experiment-design")])

        class FakeStdio:
            def __init__(self, params):
                captured["params_args"] = params.args
            async def __aenter__(self):
                return ("r", "w")
            async def __aexit__(self, *args):
                pass

        def fake_stdio_client(params):
            return FakeStdio(params)

        class FakeClientSession:
            def __init__(self, read, write):
                self._s = FakeSession()
            async def __aenter__(self):
                return self._s
            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr(onboarding, "_FastskillsStdioClient", fake_stdio_client, raising=False)
        monkeypatch.setattr(onboarding, "_FastskillsClientSession", FakeClientSession, raising=False)

        result = asyncio.run(onboarding._install_cloud_skill_for_employee(
            "00099", "https://github.com/x/y/tree/main/z",
        ))

        assert "ok install" in result
        assert captured["tool"] == "install_cloud_skill"
        assert captured["args"] == {"skill_url": "https://github.com/x/y/tree/main/z"}
        # The fastskills subprocess must point at the employee's own skills_dir
        args = captured["params_args"]
        assert "--skills-dir" in args
        skills_dir_idx = args.index("--skills-dir")
        assert str(tmp_path / "00099" / "skills") in args[skills_dir_idx + 1]


# ---------------------------------------------------------------------------
# Extra coverage — branch tests
# ---------------------------------------------------------------------------

class TestAssembleSpecialistBranches:
    def _patch_deps(self, monkeypatch, *, nickname_raises=None, hire_raises=None):
        from onemancompany.core import config as cfg
        from onemancompany.agents import common_tools, onboarding

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "sk_live_test", raising=False)
        monkeypatch.setattr(cfg.settings, "default_api_provider", "openrouter", raising=False)

        async def fake_generate_nickname(name, role, is_founding=False):
            if nickname_raises:
                raise nickname_raises
            return "Test"

        async def fake_execute_hire(**kwargs):
            if hire_raises:
                raise hire_raises
            return SimpleNamespace(id="00099", name=kwargs.get("name"))

        async def fake_install(employee_id, skill_github_url, api_key=""):
            return "installed"

        monkeypatch.setattr(onboarding, "generate_nickname", fake_generate_nickname)
        monkeypatch.setattr(onboarding, "execute_hire", fake_execute_hire)
        monkeypatch.setattr(onboarding, "_install_cloud_skill_for_employee", fake_install)
        return common_tools

    def test_nickname_timeout_falls_back_to_name_prefix(self, monkeypatch, tmp_path):
        common_tools = self._patch_deps(monkeypatch, nickname_raises=asyncio.TimeoutError())
        from onemancompany.agents import onboarding
        monkeypatch.setattr(onboarding, "EMPLOYEES_DIR", tmp_path, raising=False)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr Alex Causal",
            "role": "X",
            "skill_github_url": "https://github.com/a/b/tree/main/c",
        }))
        assert out["status"] == "ok"
        # name.split()[0][:8] of "Dr Alex Causal" → "Dr"
        assert out["nickname"] == "Dr"

    def test_nickname_runtime_error_falls_back(self, monkeypatch, tmp_path):
        common_tools = self._patch_deps(monkeypatch, nickname_raises=RuntimeError("boom"))
        from onemancompany.agents import onboarding
        monkeypatch.setattr(onboarding, "EMPLOYEES_DIR", tmp_path, raising=False)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "", "role": "X",  # empty name → "Expert" fallback
            "skill_github_url": "https://github.com/a/b/tree/main/c",
        }))
        assert out["status"] == "ok"
        assert out["nickname"] == "Expert"

    def test_hire_failure_returns_tool_error(self, monkeypatch, tmp_path):
        common_tools = self._patch_deps(monkeypatch, hire_raises=RuntimeError("disk full"))

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr X", "role": "Y",
            "skill_github_url": "https://github.com/a/b/tree/main/c",
        }))
        assert out.get("is_error") is True
        assert "hire failed" in out["message"]
        assert "disk full" in out["message"]

    def test_work_principles_persisted_when_provided(self, monkeypatch, tmp_path):
        common_tools = self._patch_deps(monkeypatch)
        # Function does an inline `from ... import EMPLOYEES_DIR`, so patch the
        # config module directly (not common_tools).
        from onemancompany.core import config as cfg
        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path, raising=False)
        # Ensure the employee dir exists so write_text_utf can write
        (tmp_path / "00099").mkdir(parents=True, exist_ok=True)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr Z", "role": "R",
            "skill_github_url": "https://github.com/a/b/tree/main/c",
            "work_principles": "Be rigorous, demand pre-registration.",
        }))
        assert out["status"] == "ok"
        wp_file = tmp_path / "00099" / "work_principles.md"
        assert wp_file.exists()
        assert "rigorous" in wp_file.read_text(encoding="utf-8")

    def test_work_principles_write_failure_does_not_abort(self, monkeypatch, tmp_path):
        common_tools = self._patch_deps(monkeypatch)
        from onemancompany.core import config as cfg
        # Patch the symbol in BOTH modules — the @tool resolves it via inline import
        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path / "nope", raising=False)

        # Make write_text_utf in common_tools blow up — the symbol is imported at
        # module level there, so patching common_tools is correct.
        from onemancompany.agents import common_tools as ct
        def fake_write(path, content):
            raise PermissionError("denied")
        monkeypatch.setattr(ct, "write_text_utf", fake_write, raising=False)

        out = asyncio.run(common_tools.assemble_specialist_from_skill.ainvoke({
            "name": "Dr Z", "role": "R",
            "skill_github_url": "https://github.com/a/b/tree/main/c",
            "work_principles": "ignored",
        }))
        # Hire + install still succeeded despite work_principles failure
        assert out["status"] == "ok"
        assert out["employee_id"] == "00099"


# ---------------------------------------------------------------------------
# search_skillsmp — @tool exposing SkillsMP search to LangChain agents
# (fastskills MCP isn't available to company-hosted employees, so we wrap it
#  as a short-lived stdio subprocess — same pattern as install)
# ---------------------------------------------------------------------------

class TestSearchSkillsmpTool:
    def test_missing_key_returns_error(self, monkeypatch):
        from onemancompany.core import config as cfg
        from onemancompany.agents import common_tools

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "", raising=False)

        out = asyncio.run(common_tools.search_skillsmp.ainvoke({"query": "anything"}))
        assert out.get("is_error") is True
        assert "SKILLSMP_API_KEY" in out["message"]

    def test_happy_path_returns_raw_results(self, monkeypatch):
        from onemancompany.core import config as cfg
        from onemancompany.agents import common_tools, onboarding

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "sk_live_test", raising=False)

        canned = "Found 2 cloud skill(s) for 'foo':\n\n1. foo-skill — ...\n   github: https://github.com/x/y/tree/main/foo\n2. bar-skill — ...\n   github: https://github.com/x/y/tree/main/bar"

        async def fake_search(query, api_key=""):
            assert query == "causal inference"
            return canned

        monkeypatch.setattr(onboarding, "_search_cloud_skills_via_fastskills", fake_search)

        out = asyncio.run(common_tools.search_skillsmp.ainvoke({"query": "causal inference"}))
        assert out["status"] == "ok"
        assert out["query"] == "causal inference"
        assert "foo-skill" in out["raw_results"]
        assert "github.com/x/y/tree/main/foo" in out["raw_results"]

    def test_subprocess_failure_returns_error(self, monkeypatch):
        from onemancompany.core import config as cfg
        from onemancompany.agents import common_tools, onboarding

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "sk_live_test", raising=False)

        async def fake_search(query, api_key=""):
            raise RuntimeError("fastskills crashed")

        monkeypatch.setattr(onboarding, "_search_cloud_skills_via_fastskills", fake_search)

        out = asyncio.run(common_tools.search_skillsmp.ainvoke({"query": "x"}))
        assert out.get("is_error") is True
        assert "search failed" in out["message"]
        assert "fastskills crashed" in out["message"]


class TestSearchCloudSkillsViaFastskills:
    def test_missing_api_key_raises(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import config as cfg

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "", raising=False)

        with pytest.raises(RuntimeError, match="SKILLSMP_API_KEY"):
            asyncio.run(onboarding._search_cloud_skills_via_fastskills("anything"))

    def test_invokes_search_cloud_skills_via_mcp(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import config as cfg

        monkeypatch.setattr(cfg.settings, "skillsmp_api_key", "sk_live_test", raising=False)

        captured = {}

        class FakeSession:
            async def initialize(self):
                pass
            async def call_tool(self, name, args):
                captured["tool"] = name
                captured["args"] = args
                return SimpleNamespace(content=[SimpleNamespace(text="Found 3 cloud skill(s) for ...")])

        class FakeStdio:
            def __init__(self, params):
                captured["params_args"] = params.args
            async def __aenter__(self):
                return ("r", "w")
            async def __aexit__(self, *args):
                pass

        def fake_stdio_client(params):
            return FakeStdio(params)

        class FakeClientSession:
            def __init__(self, read, write):
                self._s = FakeSession()
            async def __aenter__(self):
                return self._s
            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr(onboarding, "_FastskillsStdioClient", fake_stdio_client, raising=False)
        monkeypatch.setattr(onboarding, "_FastskillsClientSession", FakeClientSession, raising=False)

        result = asyncio.run(onboarding._search_cloud_skills_via_fastskills("causal RCT"))

        assert "Found 3 cloud skill" in result
        assert captured["tool"] == "search_cloud_skills"
        assert captured["args"] == {"query": "causal RCT"}
