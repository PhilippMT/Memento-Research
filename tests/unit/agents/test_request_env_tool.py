"""Tests for the ``request_env`` LangChain @tool — the agent-facing
wrapper around :mod:`onemancompany.core.env_manager`. The tool name and
schema are what the LLM sees, so we lock both."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class TestRequestEnvTool:
    def test_tool_registered_with_expected_name(self):
        from onemancompany.agents.common_tools import request_env
        assert request_env.name == "request_env"

    @pytest.mark.asyncio
    async def test_tool_returns_values_from_env_manager(self, tmp_path, monkeypatch):
        """End-to-end: tool returns the dict env_manager resolved."""
        from onemancompany.agents.common_tools import request_env
        from onemancompany.core import env_manager as em

        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")
        monkeypatch.setattr("onemancompany.core.env_manager._env_path", lambda: env_path)
        em._pending.clear()
        em._known_keys.clear()

        async def runner():
            return await request_env.ainvoke({
                "keys": [{"name": "TOOL_TEST_KEY"}],
                "reason": "unit test",
                "employee_id": "00004",
            })

        task = asyncio.create_task(runner())
        await asyncio.sleep(0.05)
        em.save_env({"TOOL_TEST_KEY": "v"})
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result["status"] == "ok"
        assert result["values"] == {"TOOL_TEST_KEY": "v"}

    @pytest.mark.asyncio
    async def test_tool_rejects_missing_employee_id(self):
        from onemancompany.agents.common_tools import request_env
        result = await request_env.ainvoke({
            "keys": [{"name": "X"}],
            "reason": "test",
            "employee_id": "",
        })
        assert "error" in result or result.get("status") == "error"
