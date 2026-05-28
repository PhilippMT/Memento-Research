"""Multi-Agent Debate (MAD) — synchronous parallel debate sessions.

Each round: all agents respond simultaneously based on the full history of
previous rounds. Debate ends when all agents reach consensus or max_rounds
is hit. A judge synthesizes the final conclusion.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from loguru import logger

from onemancompany.agents.base import make_llm, tracked_ainvoke
from onemancompany.core.config import MAX_PRINCIPLES_LEN, PF_DEPARTMENT, PF_LEVEL, PF_NAME, PF_NICKNAME, PF_ROLE, PF_WORK_PRINCIPLES


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DebateParticipantSuggestion:
    """A single participant suggestion returned by the selector."""

    employee_id: str
    name: str
    nickname: str
    role: str
    department: str
    expected_stance: str  # LLM's prediction of this person's likely position

    def to_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "nickname": self.nickname,
            "role": self.role,
            "department": self.department,
            "expected_stance": self.expected_stance,
        }


@dataclass
class DebateRound:
    round_num: int
    responses: list[dict]  # [{"agent_id", "agent_name", "content"}]

    def to_dict(self) -> dict:
        return {"round_num": self.round_num, "responses": self.responses}


@dataclass
class DebateResult:
    topic: str
    participants: list[str]
    rounds: list[DebateRound]
    conclusion: str
    consensus_reached: bool
    total_rounds: int

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "participants": self.participants,
            "rounds": [r.to_dict() for r in self.rounds],
            "conclusion": self.conclusion,
            "consensus_reached": self.consensus_reached,
            "total_rounds": self.total_rounds,
        }


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _format_current_round_so_far(responses: list[dict]) -> str:
    if not responses:
        return "(No one has spoken yet in this round.)"
    parts = []
    for resp in responses:
        parts.append(f"  {resp['agent_name']}: {resp['content']}")
    return "\n".join(parts)


def _format_round_history(history: list[DebateRound]) -> str:
    if not history:
        return "(This is the first round — no previous discussion.)"
    parts = []
    for rnd in history:
        parts.append(f"Round {rnd.round_num}:")
        for resp in rnd.responses:
            parts.append(f"  {resp['agent_name']}: {resp['content']}")
    return "\n".join(parts)


def _build_agent_prompt(
    emp_data: dict,
    emp_id: str,
    topic: str,
    history: list[DebateRound],
    round_num: int,
) -> str:
    name = emp_data.get(PF_NAME, "")
    nickname = emp_data.get(PF_NICKNAME, "")
    role = emp_data.get(PF_ROLE, "")
    dept = emp_data.get(PF_DEPARTMENT, "")
    level = emp_data.get(PF_LEVEL, 1)
    principles = emp_data.get(PF_WORK_PRINCIPLES, "")
    principles_ctx = f"\nYour work principles:\n{principles[:MAX_PRINCIPLES_LEN]}\n" if principles else ""

    history_text = _format_round_history(history)

    return (
        f"You are {name} ({nickname}, Department: {dept}, {role}, Lv.{level}).\n"
        f"{principles_ctx}"
        f"You are participating in a structured multi-agent debate.\n"
        f"Debate topic: {topic}\n"
        f"Current round: Round {round_num}\n\n"
        f"Previous rounds:\n{history_text}\n\n"
        f"Based on the above discussion and your expertise, state your position clearly and concisely "
        f"(3-5 sentences). Reference or respond to others' arguments where relevant. "
        f"Be direct — this is a debate, not a meeting."
    )


def _build_sequential_agent_prompt(
    emp_data: dict,
    emp_id: str,
    topic: str,
    history: list[DebateRound],
    current_round_so_far: list[dict],
    round_num: int,
) -> str:
    name = emp_data.get(PF_NAME, "")
    nickname = emp_data.get(PF_NICKNAME, "")
    role = emp_data.get(PF_ROLE, "")
    dept = emp_data.get(PF_DEPARTMENT, "")
    level = emp_data.get(PF_LEVEL, 1)
    principles = emp_data.get(PF_WORK_PRINCIPLES, "")
    principles_ctx = f"\nYour work principles:\n{principles[:MAX_PRINCIPLES_LEN]}\n" if principles else ""

    history_text = _format_round_history(history)
    current_text = _format_current_round_so_far(current_round_so_far)

    return (
        f"You are {name} ({nickname}, Department: {dept}, {role}, Lv.{level}).\n"
        f"{principles_ctx}"
        f"You are participating in a structured multi-agent debate.\n"
        f"Debate topic: {topic}\n"
        f"Current round: Round {round_num}\n\n"
        f"Previous rounds:\n{history_text}\n\n"
        f"Responses so far this round:\n{current_text}\n\n"
        f"Based on the above discussion and your expertise, state your position clearly and concisely "
        f"(3-5 sentences). Reference or respond to others' arguments where relevant. "
        f"Be direct — this is a debate, not a meeting."
    )


def _build_consensus_check_prompt(responses: list[dict], topic: str) -> str:
    if not responses:
        return "Responses: (none)\nHave all agents reached consensus? Reply YES or NO on the first line."

    lines = "\n".join(
        f"  {r['agent_name']}: {r['content']}" for r in responses
    )
    return (
        f"Debate topic: {topic}\n\n"
        f"Latest round responses:\n{lines}\n\n"
        f"Have all participants reached a clear consensus or agreement? "
        f"Reply YES or NO on the first line, then one sentence of reasoning."
    )


def _build_judge_prompt(topic: str, rounds: list[DebateRound], participants: list[str]) -> str:
    all_rounds_text = []
    for rnd in rounds:
        all_rounds_text.append(f"Round {rnd.round_num}:")
        for resp in rnd.responses:
            all_rounds_text.append(f"  {resp['agent_name']}: {resp['content']}")
    rounds_text = "\n".join(all_rounds_text)

    return (
        f"You are the judge of a multi-agent debate.\n"
        f"Debate topic: {topic}\n\n"
        f"Full debate transcript:\n{rounds_text}\n\n"
        f"Synthesize the key arguments from all participants and deliver a final conclusion. "
        f"Your conclusion should:\n"
        f"1. Summarize the strongest points from each side\n"
        f"2. Identify areas of agreement and disagreement\n"
        f"3. Provide a clear final recommendation or verdict\n"
        f"Be concise (4-6 sentences total)."
    )


# ---------------------------------------------------------------------------
# Participant selector
# ---------------------------------------------------------------------------


def _build_selector_prompt(
    topic: str,
    employees: dict[str, dict],
    num_participants: int | None,
) -> str:
    roster = "\n".join(
        f"  - ID: {eid}, Name: {edata.get(PF_NAME, '')} ({edata.get(PF_NICKNAME, '')}), "
        f"Role: {edata.get(PF_ROLE, '')}, Dept: {edata.get(PF_DEPARTMENT, '')}"
        for eid, edata in employees.items()
    )

    if num_participants is not None:
        count_instruction = (
            f"Select exactly {num_participants} participants."
        )
    else:
        count_instruction = (
            "Decide how many participants would make for the most productive debate "
            "(minimum 2, recommend 3-5). Select that many."
        )

    return (
        f"You are a neutral debate organiser. Your task is to select the best participants "
        f"for a structured multi-agent debate.\n\n"
        f"Debate topic: {topic}\n\n"
        f"Available employees:\n{roster}\n\n"
        f"{count_instruction} Choose people whose roles and perspectives are likely to produce "
        f"diverse, opposing, or complementary viewpoints on this topic. "
        f"Avoid selecting people with identical roles or departments unless necessary.\n\n"
        f"Return ONLY a JSON array — no prose, no markdown fences — in this exact format:\n"
        f'[{{"employee_id": "XXXXX", "expected_stance": "one sentence describing their likely position"}}]\n'
    )


async def select_debate_participants(
    *,
    topic: str,
    all_employees: dict[str, dict],
    num_participants: int | None = None,
) -> list[DebateParticipantSuggestion]:
    """Use an impartial LLM to select diverse participants for a debate.

    Args:
        topic: The debate question/topic.
        all_employees: Map of employee_id → profile dict (all candidates).
        num_participants: How many to select. None = let the selector decide.

    Returns:
        Ordered list of DebateParticipantSuggestion, each with expected_stance.

    Raises:
        ValueError: If the selector LLM returns unparseable output.
    """
    logger.debug(
        "[debate] selecting participants: topic={!r}, pool={}, num={}",
        topic, list(all_employees.keys()), num_participants,
    )
    llm = make_llm("")  # impartial — no employee persona
    prompt = _build_selector_prompt(topic, all_employees, num_participants)
    resp = await llm.ainvoke(prompt)
    raw = resp.content.strip()

    # Extract JSON array — tolerate minor surrounding whitespace or stray text
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Selector LLM returned unparseable output: {raw[:200]!r}")

    try:
        items: list[dict] = json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ValueError(f"Selector LLM returned invalid JSON: {e}") from e

    suggestions: list[DebateParticipantSuggestion] = []
    for item in items:
        eid = item.get("employee_id", "")
        emp = all_employees.get(eid)
        if not emp:
            logger.debug("[debate] selector returned unknown employee_id {!r}, skipping", eid)
            continue
        suggestions.append(DebateParticipantSuggestion(
            employee_id=eid,
            name=emp.get(PF_NAME, ""),
            nickname=emp.get(PF_NICKNAME, ""),
            role=emp.get(PF_ROLE, ""),
            department=emp.get(PF_DEPARTMENT, ""),
            expected_stance=item.get("expected_stance", ""),
        ))

    logger.debug("[debate] selector chose: {}", [s.employee_id for s in suggestions])
    return suggestions


# ---------------------------------------------------------------------------
# Consensus detection
# ---------------------------------------------------------------------------


async def _check_consensus(llm, responses: list[dict], topic: str) -> bool:
    if len(responses) < 2:
        return False
    prompt = _build_consensus_check_prompt(responses, topic)
    resp = await llm.ainvoke(prompt)
    first_line = resp.content.strip().split("\n")[0].upper()[:10]
    result = "YES" in first_line
    logger.debug("[debate] consensus check → {}", result)
    return result


# ---------------------------------------------------------------------------
# Core session runner
# ---------------------------------------------------------------------------


async def run_debate_session(
    *,
    topic: str,
    participant_ids: list[str],
    agents_data: dict[str, dict],
    max_rounds: int,
    judge_id: str = "",
    mode: str = "parallel",
    on_message: Callable[[dict], Awaitable[None]] | None,
) -> DebateResult:
    """Run a full debate session. Returns DebateResult.

    Args:
        topic: The debate question/topic.
        participant_ids: Ordered list of agent IDs who will debate.
        agents_data: Map of agent_id → employee profile dict.
        max_rounds: Maximum number of rounds before forcing judge.
        judge_id: Optional. If set, this employee's LLM is used for judging and
            consensus checks — their role/persona will influence the conclusion.
            If empty (default), an impartial anonymous judge LLM is used instead
            (company default model, no persona), eliminating participant bias.
        mode: "parallel" (default) — all agents respond simultaneously each round.
            "sequential" — agents respond one by one; each agent sees the responses
            of those who spoke earlier in the same round before composing their reply.
        on_message: Async callback called for each message event (for real-time push).
    """
    if len(participant_ids) < 2:
        raise ValueError("Debate requires at least 2 participants.")

    # judge_id="" → anonymous impartial judge using company default LLM
    # judge_id set → named employee acts as judge (their persona influences conclusion)
    impartial = not judge_id or judge_id not in agents_data
    judge_llm = make_llm("" if impartial else judge_id)
    if impartial:
        judge_name = "Judge"
        judge_employee_id = ""
    else:
        judge_data = agents_data[judge_id]
        judge_name = judge_data.get(PF_NICKNAME, "") or judge_data.get(PF_NAME, "Judge")
        judge_employee_id = judge_id

    logger.debug(
        "[debate] starting: topic={!r}, participants={}, max_rounds={}, judge={}",
        topic, participant_ids, max_rounds, judge_name,
    )

    async def _emit(speaker: str, role: str, content: str, msg_type: str = "debate_chat") -> None:
        if on_message:
            from datetime import datetime
            await on_message({
                "type": msg_type,
                "speaker": speaker,
                "role": role,
                "content": content,
                "time": datetime.now().strftime("%H:%M:%S"),
            })

    rounds: list[DebateRound] = []
    consensus_reached = False

    for round_num in range(1, max_rounds + 1):
        logger.debug("[debate] round {}/{}", round_num, max_rounds)
        await _emit("SYSTEM", "system", f"── Round {round_num} ──", "debate_round_start")

        round_responses: list[dict] = []

        if mode == "sequential":
            for agent_id in participant_ids:
                emp_data = agents_data[agent_id]
                prompt = _build_sequential_agent_prompt(
                    emp_data, agent_id, topic, rounds, round_responses, round_num,
                )
                llm = make_llm(agent_id)
                try:
                    resp = await tracked_ainvoke(llm, prompt, category="debate", employee_id=agent_id)
                except Exception as exc:
                    logger.warning("[debate] agent {} response failed: {}", agent_id, exc)
                    continue
                entry = {
                    "agent_id": agent_id,
                    "agent_name": emp_data.get(PF_NICKNAME, "") or emp_data.get(PF_NAME, agent_id),
                    "content": resp.content,
                }
                round_responses.append(entry)
                await _emit(entry["agent_name"], "debater", entry["content"])
        else:
            # All agents respond in parallel (default)
            async def _agent_respond(agent_id: str) -> dict:
                emp_data = agents_data[agent_id]
                prompt = _build_agent_prompt(emp_data, agent_id, topic, rounds, round_num)
                llm = make_llm(agent_id)
                resp = await tracked_ainvoke(llm, prompt, category="debate", employee_id=agent_id)
                return {
                    "agent_id": agent_id,
                    "agent_name": emp_data.get(PF_NICKNAME, "") or emp_data.get(PF_NAME, agent_id),
                    "content": resp.content,
                }

            results = await asyncio.gather(
                *[_agent_respond(pid) for pid in participant_ids],
                return_exceptions=True,
            )

            for r in results:
                if isinstance(r, Exception):
                    logger.warning("[debate] agent response failed: {}", r)
                    continue
                round_responses.append(r)
                await _emit(r["agent_name"], "debater", r["content"])

        rounds.append(DebateRound(round_num=round_num, responses=round_responses))

        # Check for consensus after each round (skip if last round — judge handles it)
        if round_num < max_rounds:
            consensus_reached = await _check_consensus(judge_llm, round_responses, topic)
            if consensus_reached:
                logger.debug("[debate] consensus reached at round {}", round_num)
                await _emit("SYSTEM", "system", "Consensus reached — moving to final judgment.", "debate_consensus")
                break

    # Judge synthesizes conclusion
    judge_prompt = _build_judge_prompt(topic, rounds, participant_ids)
    judge_resp = await tracked_ainvoke(
        judge_llm, judge_prompt, category="debate_judge", employee_id=judge_employee_id,
    )
    conclusion = judge_resp.content

    await _emit(judge_name, "judge", conclusion, "debate_conclusion")
    logger.debug("[debate] session complete, rounds={}, consensus={}", len(rounds), consensus_reached)

    return DebateResult(
        topic=topic,
        participants=participant_ids,
        rounds=rounds,
        conclusion=conclusion,
        consensus_reached=consensus_reached,
        total_rounds=len(rounds),
    )
