"""Unit tests for debate tools in agents/common_tools.py.

Covers run_debate and select_debate_participants_tool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState, Employee


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cs() -> CompanyState:
    cs = CompanyState()
    cs.employees = {}
    cs.ex_employees = {}
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname=f"Nick{emp_id}",
        department="Eng", level=2,
    )
    defaults.update(kwargs)
    return Employee(**defaults)


def _emp_to_dict(emp: Employee) -> dict:
    d: dict = {}
    for field in ("id", "name", "nickname", "role", "skills", "level", "department",
                  "permissions", "tool_permissions", "work_principles"):
        val = getattr(emp, field, None)
        if val is not None:
            d[field] = val
    d["runtime"] = {"status": "idle", "is_listening": False, "current_task_summary": ""}
    return d


def _mock_store(monkeypatch, cs) -> None:
    from onemancompany.agents import common_tools as ct_mod

    def _fake_load_employee(emp_id: str) -> dict:
        emp = cs.employees.get(emp_id)
        return _emp_to_dict(emp) if emp else {}

    def _fake_load_all() -> dict[str, dict]:
        return {eid: _emp_to_dict(e) for eid, e in cs.employees.items()}

    monkeypatch.setattr(ct_mod, "load_employee", _fake_load_employee)
    monkeypatch.setattr(ct_mod, "load_all_employees", _fake_load_all)


# ---------------------------------------------------------------------------
# run_debate tool
# ---------------------------------------------------------------------------

class TestRunDebateTool:
    @pytest.mark.asyncio
    async def test_returns_error_when_fewer_than_two_valid_participants(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        result = await ct_mod.run_debate.ainvoke({
            "topic": "Test topic",
            "participant_ids": ["00100"],  # only one valid
        })
        assert result["status"] == "error"
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_returns_error_when_all_participants_unknown(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        result = await ct_mod.run_debate.ainvoke({
            "topic": "Topic",
            "participant_ids": ["99998", "99999"],
        })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_successful_debate_returns_completed_status(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.debate import DebateResult, DebateRound

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        cs.employees["00101"] = _make_emp("00101")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        fake_result = DebateResult(
            topic="Should we rewrite in Go?",
            participants=["00100", "00101"],
            rounds=[DebateRound(round_num=1, responses=[
                {"agent_id": "00100", "agent_name": "Nick00100", "content": "Yes"},
                {"agent_id": "00101", "agent_name": "Nick00101", "content": "No"},
            ])],
            conclusion="Final verdict.",
            consensus_reached=False,
            total_rounds=1,
        )

        with patch("onemancompany.agents.common_tools.run_debate_session", new_callable=AsyncMock) as mock_session, \
             patch("onemancompany.agents.common_tools._chat", new_callable=AsyncMock):
            mock_session.return_value = fake_result

            result = await ct_mod.run_debate.ainvoke({
                "topic": "Should we rewrite in Go?",
                "participant_ids": ["00100", "00101"],
                "max_rounds": 3,
            })

        assert result["status"] == "completed"
        assert result["topic"] == "Should we rewrite in Go?"
        assert result["conclusion"] == "Final verdict."
        assert result["total_rounds"] == 1
        assert len(result["rounds"]) == 1

    @pytest.mark.asyncio
    async def test_ignores_invalid_participant_ids_uses_valid_ones(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.debate import DebateResult, DebateRound

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        cs.employees["00101"] = _make_emp("00101")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        fake_result = DebateResult(
            topic="Topic", participants=["00100", "00101"],
            rounds=[], conclusion="Verdict.", consensus_reached=True, total_rounds=0,
        )

        captured_ids: list = []

        async def _fake_session(**kwargs):
            captured_ids.extend(kwargs["participant_ids"])
            return fake_result

        with patch("onemancompany.agents.common_tools.run_debate_session", side_effect=_fake_session), \
             patch("onemancompany.agents.common_tools._chat", new_callable=AsyncMock):
            await ct_mod.run_debate.ainvoke({
                "topic": "Topic",
                "participant_ids": ["00100", "99999", "00101"],  # 99999 is invalid
            })

        assert "00100" in captured_ids
        assert "00101" in captured_ids
        assert "99999" not in captured_ids

    @pytest.mark.asyncio
    async def test_session_exception_returns_error(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        cs.employees["00101"] = _make_emp("00101")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        with patch("onemancompany.agents.common_tools.run_debate_session",
                   new_callable=AsyncMock) as mock_session, \
             patch("onemancompany.agents.common_tools._chat", new_callable=AsyncMock):
            mock_session.side_effect = RuntimeError("LLM unavailable")

            result = await ct_mod.run_debate.ainvoke({
                "topic": "Topic",
                "participant_ids": ["00100", "00101"],
            })

        assert result["status"] == "error"
        assert "LLM unavailable" in result["message"]


# ---------------------------------------------------------------------------
# select_debate_participants_tool
# ---------------------------------------------------------------------------

class TestSelectDebateParticipantsTool:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_employees(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        result = await ct_mod.select_debate_participants_tool.ainvoke({
            "topic": "Topic",
        })
        assert result["status"] == "error"
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_returns_suggestions_and_participant_ids(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.debate import DebateParticipantSuggestion

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100", name="Alice")
        cs.employees["00101"] = _make_emp("00101", name="Bob")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        fake_suggestions = [
            DebateParticipantSuggestion(
                employee_id="00100", name="Alice", nickname="NickAlice",
                role="Engineer", department="Eng",
                expected_stance="Favours speed",
            ),
            DebateParticipantSuggestion(
                employee_id="00101", name="Bob", nickname="NickBob",
                role="Engineer", department="Eng",
                expected_stance="Favours quality",
            ),
        ]

        with patch("onemancompany.agents.common_tools.select_debate_participants",
                   new_callable=AsyncMock) as mock_select:
            mock_select.return_value = fake_suggestions

            result = await ct_mod.select_debate_participants_tool.ainvoke({
                "topic": "Speed vs quality",
                "num_participants": 2,
            })

        assert result["status"] == "ok"
        assert result["topic"] == "Speed vs quality"
        assert len(result["suggestions"]) == 2
        assert result["participant_ids"] == ["00100", "00101"]
        assert result["suggestions"][0]["expected_stance"] == "Favours speed"

    @pytest.mark.asyncio
    async def test_passes_none_when_num_participants_is_zero(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.debate import DebateParticipantSuggestion

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        captured_kwargs: dict = {}

        async def _fake_select(**kwargs):
            captured_kwargs.update(kwargs)
            return [DebateParticipantSuggestion(
                employee_id="00100", name="E", nickname="N",
                role="R", department="D", expected_stance="S",
            )]

        with patch("onemancompany.agents.common_tools.select_debate_participants",
                   side_effect=_fake_select):
            await ct_mod.select_debate_participants_tool.ainvoke({
                "topic": "Topic",
                "num_participants": 0,  # should translate to None
            })

        assert captured_kwargs.get("num_participants") is None

    @pytest.mark.asyncio
    async def test_selector_value_error_returns_error(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        with patch("onemancompany.agents.common_tools.select_debate_participants",
                   new_callable=AsyncMock) as mock_select:
            mock_select.side_effect = ValueError("Selector LLM returned unparseable output: 'bad'")

            result = await ct_mod.select_debate_participants_tool.ainvoke({
                "topic": "Topic",
            })

        assert result["status"] == "error"
        assert result["is_error"] is True
        assert "Selector failed" in result["message"]

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_error(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00100"] = _make_emp("00100")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        _mock_store(monkeypatch, cs)

        with patch("onemancompany.agents.common_tools.select_debate_participants",
                   new_callable=AsyncMock) as mock_select:
            mock_select.side_effect = RuntimeError("Network error")

            result = await ct_mod.select_debate_participants_tool.ainvoke({
                "topic": "Topic",
            })

        assert result["status"] == "error"
        assert "Network error" in result["message"]
