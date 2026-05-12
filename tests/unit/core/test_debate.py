"""Unit tests for core/debate.py — Multi-Agent Debate (MAD) module."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.debate import (
    DebateParticipantSuggestion,
    DebateResult,
    DebateRound,
    _build_agent_prompt,
    _build_consensus_check_prompt,
    _build_judge_prompt,
    _build_selector_prompt,
    _check_consensus,
    _format_round_history,
    run_debate_session,
    select_debate_participants,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emp(name: str = "Alice", nickname: str = "A", role: str = "Engineer",
         dept: str = "Eng", level: int = 2, principles: str = "") -> dict:
    return {
        "name": name, "nickname": nickname, "role": role,
        "department": dept, "level": level, "work_principles": principles,
    }


def _make_round(num: int, responses: list[dict]) -> DebateRound:
    return DebateRound(round_num=num, responses=responses)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class TestDebateParticipantSuggestion:
    def test_to_dict_roundtrip(self):
        s = DebateParticipantSuggestion(
            employee_id="00100", name="Alice", nickname="A",
            role="Engineer", department="Eng",
            expected_stance="Prefers gradual migration",
        )
        d = s.to_dict()
        assert d["employee_id"] == "00100"
        assert d["name"] == "Alice"
        assert d["expected_stance"] == "Prefers gradual migration"
        assert set(d.keys()) == {
            "employee_id", "name", "nickname", "role", "department", "expected_stance"
        }


class TestDebateRound:
    def test_to_dict(self):
        rnd = _make_round(1, [{"agent_id": "00100", "agent_name": "Alice", "content": "I agree"}])
        d = rnd.to_dict()
        assert d["round_num"] == 1
        assert len(d["responses"]) == 1
        assert d["responses"][0]["content"] == "I agree"


class TestDebateResult:
    def test_to_dict_contains_all_fields(self):
        result = DebateResult(
            topic="Test topic",
            participants=["00100", "00101"],
            rounds=[_make_round(1, [])],
            conclusion="Final verdict.",
            consensus_reached=True,
            total_rounds=1,
        )
        d = result.to_dict()
        assert d["topic"] == "Test topic"
        assert d["consensus_reached"] is True
        assert d["total_rounds"] == 1
        assert len(d["rounds"]) == 1
        assert d["conclusion"] == "Final verdict."


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

class TestFormatRoundHistory:
    def test_empty_history(self):
        text = _format_round_history([])
        assert "first round" in text.lower()

    def test_single_round(self):
        rnd = _make_round(1, [
            {"agent_name": "Alice", "content": "Point A"},
            {"agent_name": "Bob", "content": "Point B"},
        ])
        text = _format_round_history([rnd])
        assert "Round 1" in text
        assert "Alice" in text
        assert "Point B" in text


class TestBuildAgentPrompt:
    def test_contains_topic_and_identity(self):
        emp = _emp("Alice", "A", "Engineer", "Eng", 3)
        prompt = _build_agent_prompt(emp, "00100", "Should we rewrite in Rust?", [], 1)
        assert "Alice" in prompt
        assert "Should we rewrite in Rust?" in prompt
        assert "Round 1" in prompt

    def test_includes_principles_when_present(self):
        emp = _emp(principles="Always test first.")
        prompt = _build_agent_prompt(emp, "00100", "Topic", [], 1)
        assert "Always test first." in prompt

    def test_no_principles_section_when_empty(self):
        emp = _emp(principles="")
        prompt = _build_agent_prompt(emp, "00100", "Topic", [], 1)
        assert "work principles" not in prompt.lower()

    def test_history_included_when_present(self):
        history = [_make_round(1, [{"agent_name": "Bob", "content": "Counter-point"}])]
        emp = _emp()
        prompt = _build_agent_prompt(emp, "00100", "Topic", history, 2)
        assert "Counter-point" in prompt


class TestBuildConsensusCheckPrompt:
    def test_empty_responses(self):
        prompt = _build_consensus_check_prompt([], "Topic")
        assert "YES or NO" in prompt

    def test_with_responses(self):
        responses = [
            {"agent_name": "Alice", "content": "I agree"},
            {"agent_name": "Bob", "content": "Me too"},
        ]
        prompt = _build_consensus_check_prompt(responses, "Topic")
        assert "Alice" in prompt
        assert "I agree" in prompt
        assert "YES or NO" in prompt


class TestBuildJudgePrompt:
    def test_contains_topic_and_transcript(self):
        rounds = [_make_round(1, [{"agent_name": "Alice", "content": "Good point"}])]
        prompt = _build_judge_prompt("Should we scale?", rounds, ["00100"])
        assert "Should we scale?" in prompt
        assert "Alice" in prompt
        assert "Good point" in prompt
        assert "conclusion" in prompt.lower()


class TestBuildSelectorPrompt:
    def test_with_fixed_count(self):
        employees = {
            "00100": _emp("Alice", "A", "Engineer", "Eng"),
            "00101": _emp("Bob", "B", "Designer", "Design"),
        }
        prompt = _build_selector_prompt("Topic", employees, 2)
        assert "exactly 2" in prompt
        assert "Alice" in prompt
        assert "Bob" in prompt

    def test_with_auto_count(self):
        employees = {"00100": _emp()}
        prompt = _build_selector_prompt("Topic", employees, None)
        assert "minimum 2" in prompt
        assert "Decide how many" in prompt


# ---------------------------------------------------------------------------
# Consensus detection
# ---------------------------------------------------------------------------

class TestCheckConsensus:
    @pytest.mark.asyncio
    async def test_returns_false_for_single_response(self):
        llm = AsyncMock()
        result = await _check_consensus(llm, [{"agent_name": "A", "content": "x"}], "topic")
        assert result is False
        llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_yes_returns_true(self):
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="YES\nAll agree.")
        responses = [
            {"agent_name": "A", "content": "agree"},
            {"agent_name": "B", "content": "agree"},
        ]
        result = await _check_consensus(llm, responses, "topic")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_returns_false(self):
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="NO\nStill disagreement.")
        responses = [
            {"agent_name": "A", "content": "disagree"},
            {"agent_name": "B", "content": "also disagree"},
        ]
        result = await _check_consensus(llm, responses, "topic")
        assert result is False


# ---------------------------------------------------------------------------
# select_debate_participants
# ---------------------------------------------------------------------------

class TestSelectDebateParticipants:
    @pytest.mark.asyncio
    async def test_returns_suggestions_for_valid_ids(self):
        employees = {
            "00100": _emp("Alice", "A", "Engineer", "Eng"),
            "00101": _emp("Bob", "B", "Designer", "Design"),
        }
        selector_output = json.dumps([
            {"employee_id": "00100", "expected_stance": "Prefers technical approach"},
            {"employee_id": "00101", "expected_stance": "Prefers user-centric design"},
        ])

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm:
            llm = AsyncMock()
            llm.ainvoke.return_value = MagicMock(content=selector_output)
            mock_make_llm.return_value = llm

            suggestions = await select_debate_participants(
                topic="Should we prioritise speed or design?",
                all_employees=employees,
                num_participants=2,
            )

        assert len(suggestions) == 2
        assert suggestions[0].employee_id == "00100"
        assert suggestions[0].name == "Alice"
        assert "technical" in suggestions[0].expected_stance.lower()
        assert suggestions[1].employee_id == "00101"

    @pytest.mark.asyncio
    async def test_skips_unknown_employee_ids(self):
        employees = {"00100": _emp("Alice")}
        selector_output = json.dumps([
            {"employee_id": "00100", "expected_stance": "stance A"},
            {"employee_id": "99999", "expected_stance": "stance B"},  # unknown
        ])

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm:
            llm = AsyncMock()
            llm.ainvoke.return_value = MagicMock(content=selector_output)
            mock_make_llm.return_value = llm

            suggestions = await select_debate_participants(
                topic="Topic", all_employees=employees,
            )

        assert len(suggestions) == 1
        assert suggestions[0].employee_id == "00100"

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        employees = {"00100": _emp()}

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm:
            llm = AsyncMock()
            llm.ainvoke.return_value = MagicMock(content="not json at all")
            mock_make_llm.return_value = llm

            with pytest.raises(ValueError, match="unparseable"):
                await select_debate_participants(topic="Topic", all_employees=employees)

    @pytest.mark.asyncio
    async def test_tolerates_json_embedded_in_prose(self):
        employees = {"00100": _emp("Alice")}
        selector_output = 'Sure! Here are the participants:\n[{"employee_id": "00100", "expected_stance": "favors it"}]\nHope that helps.'

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm:
            llm = AsyncMock()
            llm.ainvoke.return_value = MagicMock(content=selector_output)
            mock_make_llm.return_value = llm

            suggestions = await select_debate_participants(
                topic="Topic", all_employees=employees,
            )

        assert len(suggestions) == 1


# ---------------------------------------------------------------------------
# run_debate_session
# ---------------------------------------------------------------------------

class TestRunDebateSession:
    def _make_agents_data(self) -> dict[str, dict]:
        return {
            "00100": _emp("Alice", "A", "Engineer", "Eng"),
            "00101": _emp("Bob", "B", "Designer", "Design"),
        }

    @pytest.mark.asyncio
    async def test_raises_with_fewer_than_two_participants(self):
        with pytest.raises(ValueError, match="at least 2"):
            await run_debate_session(
                topic="Topic",
                participant_ids=["00100"],
                agents_data={"00100": _emp()},
                max_rounds=2,
                on_message=None,
            )

    @pytest.mark.asyncio
    async def test_basic_session_completes(self):
        agents_data = self._make_agents_data()

        agent_response = MagicMock(content="My argument here.")
        judge_response = MagicMock(content="Final conclusion.")
        consensus_response = MagicMock(content="NO\nStill disagree.")

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = consensus_response

            # tracked_ainvoke: first N calls = agent responses, last = judge
            call_count = 0

            async def _tracked_side_effect(llm, prompt, **kwargs):
                nonlocal call_count
                call_count += 1
                # Last call to tracked_ainvoke is the judge
                return judge_response if kwargs.get("category") == "debate_judge" else agent_response

            mock_invoke.side_effect = _tracked_side_effect
            mock_make_llm.return_value = judge_llm

            result = await run_debate_session(
                topic="Should we rewrite in Rust?",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=2,
                on_message=None,
            )

        assert result.topic == "Should we rewrite in Rust?"
        assert result.participants == ["00100", "00101"]
        assert result.total_rounds >= 1
        assert result.conclusion == "Final conclusion."
        assert isinstance(result.rounds, list)

    @pytest.mark.asyncio
    async def test_stops_early_on_consensus(self):
        agents_data = self._make_agents_data()

        agent_response = MagicMock(content="I agree.")
        judge_response = MagicMock(content="Consensus conclusion.")
        consensus_yes = MagicMock(content="YES\nAll agree.")

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = consensus_yes
            mock_make_llm.return_value = judge_llm

            async def _tracked_side_effect(llm, prompt, **kwargs):
                return judge_response if kwargs.get("category") == "debate_judge" else agent_response

            mock_invoke.side_effect = _tracked_side_effect

            result = await run_debate_session(
                topic="Agreement topic",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=5,
                on_message=None,
            )

        assert result.consensus_reached is True
        assert result.total_rounds < 5  # stopped early

    @pytest.mark.asyncio
    async def test_on_message_called_for_each_event(self):
        agents_data = self._make_agents_data()
        messages: list[dict] = []

        async def _on_message(msg: dict) -> None:
            messages.append(msg)

        agent_response = MagicMock(content="Argument.")
        judge_response = MagicMock(content="Conclusion.")
        no_consensus = MagicMock(content="NO")

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = no_consensus
            mock_make_llm.return_value = judge_llm

            async def _tracked_side_effect(llm, prompt, **kwargs):
                return judge_response if kwargs.get("category") == "debate_judge" else agent_response

            mock_invoke.side_effect = _tracked_side_effect

            await run_debate_session(
                topic="Topic",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=1,
                on_message=_on_message,
            )

        # At minimum: round_start, 2 agent responses, and judge conclusion
        assert len(messages) >= 4
        msg_types = {m["type"] for m in messages}
        assert "debate_round_start" in msg_types
        assert "debate_conclusion" in msg_types

    @pytest.mark.asyncio
    async def test_agent_failure_is_skipped_gracefully(self):
        """If one agent's LLM call fails, the round continues with remaining responses."""
        agents_data = self._make_agents_data()

        call_count = 0
        judge_response = MagicMock(content="Conclusion.")
        no_consensus = MagicMock(content="NO")

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = no_consensus
            mock_make_llm.return_value = judge_llm

            async def _tracked_side_effect(llm, prompt, **kwargs):
                nonlocal call_count
                if kwargs.get("category") == "debate_judge":
                    return judge_response
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("LLM timeout")
                return MagicMock(content="Response from second agent.")

            mock_invoke.side_effect = _tracked_side_effect

            result = await run_debate_session(
                topic="Topic",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=1,
                on_message=None,
            )

        # Session should complete despite one agent failing
        assert result.conclusion == "Conclusion."
        # Only the successful agent's response should be in the round
        assert len(result.rounds[0].responses) == 1

    @pytest.mark.asyncio
    async def test_named_judge_uses_judge_persona(self):
        """When judge_id is set and valid, the judge's name is used in the conclusion."""
        agents_data = {
            "00100": _emp("Alice", "A"),
            "00101": _emp("Bob", "B"),
            "00102": _emp("Charlie", "Charlie", "Moderator"),
        }

        judge_response = MagicMock(content="Judge conclusion.")
        no_consensus = MagicMock(content="NO")
        agent_response = MagicMock(content="Argument.")

        messages: list[dict] = []

        async def _on_message(msg: dict) -> None:
            messages.append(msg)

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = no_consensus
            mock_make_llm.return_value = judge_llm

            async def _tracked_side_effect(llm, prompt, **kwargs):
                return judge_response if kwargs.get("category") == "debate_judge" else agent_response

            mock_invoke.side_effect = _tracked_side_effect

            result = await run_debate_session(
                topic="Topic",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=1,
                judge_id="00102",
                on_message=_on_message,
            )

        conclusion_msgs = [m for m in messages if m["type"] == "debate_conclusion"]
        assert len(conclusion_msgs) == 1
        assert conclusion_msgs[0]["speaker"] == "Charlie"

    @pytest.mark.asyncio
    async def test_unknown_judge_id_falls_back_to_impartial(self):
        """judge_id not in agents_data → impartial judge, speaker='Judge'."""
        agents_data = self._make_agents_data()
        messages: list[dict] = []

        async def _on_message(msg: dict) -> None:
            messages.append(msg)

        with patch("onemancompany.core.debate.make_llm") as mock_make_llm, \
             patch("onemancompany.core.debate.tracked_ainvoke", new_callable=AsyncMock) as mock_invoke:

            judge_llm = AsyncMock()
            judge_llm.ainvoke.return_value = MagicMock(content="NO")
            mock_make_llm.return_value = judge_llm

            async def _tracked_side_effect(llm, prompt, **kwargs):
                return MagicMock(content="Judge verdict." if kwargs.get("category") == "debate_judge" else "Arg.")

            mock_invoke.side_effect = _tracked_side_effect

            await run_debate_session(
                topic="Topic",
                participant_ids=["00100", "00101"],
                agents_data=agents_data,
                max_rounds=1,
                judge_id="99999",  # not in agents_data
                on_message=_on_message,
            )

        conclusion_msgs = [m for m in messages if m["type"] == "debate_conclusion"]
        assert conclusion_msgs[0]["speaker"] == "Judge"
