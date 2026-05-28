from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.core.config import DATA_ROOT, PROJECTS_DIR


MEMORY_DIR_NAME = "research_memory"
MEMORY_FILENAME = "stage_memories.jsonl"
MAX_STORED_TEXT = 12000
MAX_SNIPPET_TEXT = 700


@dataclass(frozen=True)
class RetrievedResearchMemory:
    guidance: str
    memory_ids: list[str]
    query: str
    simmax: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, lower_bound: float = -1.0, upper_bound: float = 1.0) -> float:
    return max(lower_bound, min(upper_bound, value))


def _limit_text(text: Any, limit: int = MAX_STORED_TEXT) -> str:
    compact = str(text or "")
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 32)].rstrip() + "\n...[truncated]"


def _snippet(text: Any, limit: int = MAX_SNIPPET_TEXT) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _tokenize(text: str) -> list[str]:
    normalized = (text or "").lower()
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)


def _vectorize(text: str) -> Counter[str]:
    tokens = _tokenize(text)
    vector: Counter[str] = Counter(tokens)
    for index in range(len(tokens) - 1):
        vector[f"{tokens[index]}_{tokens[index + 1]}"] += 1
    return vector


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = left.keys() & right.keys()
    numerator = sum(left[key] * right[key] for key in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _default_memory_root(project_dir: str | Path) -> Path:
    project_path = Path(project_dir)
    try:
        if project_path.resolve().is_relative_to(PROJECTS_DIR.resolve()):
            return DATA_ROOT / MEMORY_DIR_NAME
    except OSError as exc:
        logger.warning("Failed to ...: {}", exc)
    return project_path / f".{MEMORY_DIR_NAME}"


class ResearchMemoryStore:
    """Small MemRL-style memory layer for AutoResearch pipeline stages."""

    def __init__(
        self,
        project_id: str,
        project_dir: str | Path,
        memory_root: str | Path | None = None,
    ) -> None:
        self.project_id = project_id
        self.project_dir = Path(project_dir)
        self.memory_dir = Path(memory_root) if memory_root else _default_memory_root(project_dir)
        self.memory_path = self.memory_dir / MEMORY_FILENAME

    def retrieve_stage_guidance(
        self,
        *,
        topic: str,
        stage: dict[str, Any],
        context: str,
        feedback: str = "",
        k: int = 5,
        threshold: float = 0.01,
    ) -> RetrievedResearchMemory:
        query = self._build_stage_query(topic, stage, context, feedback)
        records = self._read_records()
        if not records:
            return RetrievedResearchMemory("", [], query, 0.0)

        query_vector = _vectorize(query)
        candidates: list[dict[str, Any]] = []
        total_records = max(1, len(records))
        for index, record in enumerate(records):
            memory_text = self._record_search_text(record)
            similarity = _cosine_similarity(query_vector, _vectorize(memory_text))
            same_stage = int(record.get("stage_id", 0) or 0) == int(stage.get("id", 0) or 0)
            if similarity < threshold and not same_stage:
                continue

            q_value = _clamp(float(record.get("q_value", record.get("reward", 0.0)) or 0.0))
            q_unit = (q_value + 1.0) / 2.0
            stage_bonus = 0.12 if same_stage else 0.0
            recency = float(index + 1) / float(total_records)
            score = (0.58 * similarity) + (0.25 * q_unit) + stage_bonus + (0.05 * recency)
            enriched = dict(record)
            enriched.update(
                {
                    "similarity": similarity,
                    "q_estimate": q_value,
                    "score": score,
                    "recency": recency,
                }
            )
            candidates.append(enriched)

        if not candidates:
            return RetrievedResearchMemory("", [], query, 0.0)

        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)[:k]
        guidance = self._render_guidance(ranked)
        memory_ids = [str(item.get("id")) for item in ranked if item.get("id")]
        simmax = max(float(item.get("similarity", 0.0)) for item in candidates)
        return RetrievedResearchMemory(guidance, memory_ids, query, simmax)

    def record_stage_episode(
        self,
        *,
        topic: str,
        stage: dict[str, Any],
        producer_result: str,
        critic_result: str,
        passed: bool,
        confidence: float | None,
        retries: int,
        reward: float,
        retrieved_memory_ids: list[str] | None = None,
        outcome: str = "critic_review",
    ) -> str:
        now = _now_iso()
        stage_id = int(stage.get("id", 0) or 0)
        stage_name = str(stage.get("name", ""))
        stage_skill = str(stage.get("skill", ""))
        producer_text = _limit_text(producer_result)
        critic_text = _limit_text(critic_result)
        memory_id = self._make_memory_id(stage_id, producer_text, critic_text, now)
        q_value = _clamp(float(reward))
        record = {
            "id": memory_id,
            "created_at": now,
            "updated_at": now,
            "project_id": self.project_id,
            "topic": topic,
            "stage_id": stage_id,
            "stage_name": stage_name,
            "stage_skill": stage_skill,
            "task_description": f"Stage {stage_id}: {stage_name} for {topic}",
            "trajectory": self._build_trajectory(
                topic=topic,
                stage_id=stage_id,
                stage_name=stage_name,
                producer_result=producer_text,
                critic_result=critic_text,
                passed=passed,
                confidence=confidence,
                retries=retries,
                outcome=outcome,
            ),
            "producer_result": producer_text,
            "critic_result": critic_text,
            "passed": bool(passed),
            "success": bool(passed),
            "confidence": confidence,
            "retries": int(retries),
            "reward": q_value,
            "q_value": q_value,
            "q_visits": 1,
            "reward_ma": q_value,
            "outcome": outcome,
            "retrieved_memory_ids": list(retrieved_memory_ids or []),
            "ceo_feedback": "",
            "ceo_approved": None,
        }
        records = self._read_records()
        records.append(record)
        self._write_records(records)
        return memory_id

    def apply_ceo_feedback(
        self,
        *,
        episode_id: str | None,
        retrieved_memory_ids: list[str] | None,
        feedback: str = "",
        approved: bool,
    ) -> dict[str, Any]:
        records = self._read_records()
        if not records:
            return {"episode_id": episode_id, "updated_ids": []}

        feedback = _limit_text(feedback, 2000)
        reward_signal = 0.35 if approved else -0.55
        updated_ids: list[str] = []
        related_ids = {str(memory_id) for memory_id in (retrieved_memory_ids or []) if memory_id}
        now = _now_iso()

        for record in records:
            record_id = str(record.get("id", ""))
            if episode_id and record_id == episode_id:
                prior_reward = float(record.get("reward", 0.0) or 0.0)
                record["reward"] = _clamp(prior_reward + reward_signal)
                record["ceo_feedback"] = feedback
                record["ceo_approved"] = bool(approved)
                record["updated_at"] = now
                self._update_q(record, record["reward"])
                updated_ids.append(record_id)
                continue

            if record_id in related_ids:
                record["updated_at"] = now
                record["last_used_at"] = now
                self._update_q(record, reward_signal)
                updated_ids.append(record_id)

        if updated_ids:
            self._write_records(records)
        return {"episode_id": episode_id, "updated_ids": updated_ids, "reward_signal": reward_signal}

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.memory_path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            with self.memory_path.open("r", encoding="utf-8") as memory_file:
                for line in memory_file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.debug("Skipping corrupt research memory line: {}", exc)
                        continue
                    if isinstance(record, dict):
                        records.append(record)
        except OSError as exc:
            logger.warning("Unable to read research memory {}: {}", self.memory_path, exc)
        return records

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.memory_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as memory_file:
            for record in records:
                memory_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                memory_file.write("\n")
        tmp_path.replace(self.memory_path)

    def _build_stage_query(
        self,
        topic: str,
        stage: dict[str, Any],
        context: str,
        feedback: str,
    ) -> str:
        parts = [
            f"Research topic: {topic}",
            f"Stage {stage.get('id')}: {stage.get('name', '')}",
            f"Skill: {stage.get('skill', '')}",
            _snippet(context, 2500),
        ]
        if feedback:
            parts.append(f"Revision feedback: {_snippet(feedback, 1000)}")
        return "\n".join(part for part in parts if part)

    def _record_search_text(self, record: dict[str, Any]) -> str:
        fields = [
            record.get("task_description", ""),
            record.get("topic", ""),
            record.get("stage_name", ""),
            record.get("stage_skill", ""),
            record.get("trajectory", ""),
            record.get("critic_result", ""),
        ]
        return "\n".join(str(field or "") for field in fields)

    def _render_guidance(self, ranked: list[dict[str, Any]]) -> str:
        positives = [item for item in ranked if float(item.get("q_estimate", 0.0) or 0.0) >= 0]
        negatives = [item for item in ranked if float(item.get("q_estimate", 0.0) or 0.0) < 0]
        lines = [
            "MemRL-style retrieved lessons from previous research stages.",
            "Use these as operational guidance; do not treat them as source evidence.",
            "",
        ]
        if positives:
            lines.append("Useful prior memories:")
            for index, item in enumerate(positives[:3], start=1):
                lines.extend(self._format_guidance_item(index, item, positive=True))
            lines.append("")
        if negatives:
            lines.append("Negative memories to avoid:")
            for index, item in enumerate(negatives[:2], start=1):
                lines.extend(self._format_guidance_item(index, item, positive=False))
            lines.append("")
        return "\n".join(lines).strip()

    def _format_guidance_item(
        self,
        index: int,
        item: dict[str, Any],
        *,
        positive: bool,
    ) -> list[str]:
        q_value = float(item.get("q_estimate", 0.0) or 0.0)
        similarity = float(item.get("similarity", 0.0) or 0.0)
        stage_label = f"Stage {item.get('stage_id', '?')}: {item.get('stage_name', '')}"
        topic = _snippet(item.get("topic", ""), 140)
        source = item.get("critic_result") or item.get("trajectory") or item.get("producer_result")
        verb = "Reuse signal" if positive else "Avoid signal"
        return [
            f"{index}. {stage_label} | q={q_value:.2f} sim={similarity:.2f} | topic: {topic}",
            f"   {verb}: {_snippet(source, 420)}",
        ]

    def _build_trajectory(
        self,
        *,
        topic: str,
        stage_id: int,
        stage_name: str,
        producer_result: str,
        critic_result: str,
        passed: bool,
        confidence: float | None,
        retries: int,
        outcome: str,
    ) -> str:
        decision = "PASS" if passed else "REJECT"
        confidence_text = "unknown" if confidence is None else f"{confidence:.2f}"
        return (
            f"SCRIPT:\n"
            f"For similar Stage {stage_id} ({stage_name}) work, use this outcome as "
            f"{'positive' if passed else 'negative'} process evidence.\n\n"
            f"TRAJECTORY:\n"
            f"topic: {topic}\n"
            f"stage: {stage_id} {stage_name}\n"
            f"decision: {decision}\n"
            f"confidence: {confidence_text}\n"
            f"retries_before_review: {retries}\n"
            f"outcome: {outcome}\n\n"
            f"PRODUCER_OUTPUT:\n{producer_result}\n\n"
            f"CRITIC_REVIEW:\n{critic_result}\n"
        )

    def _make_memory_id(self, stage_id: int, producer_result: str, critic_result: str, created_at: str) -> str:
        digest = hashlib.sha1(
            f"{self.project_id}\n{stage_id}\n{created_at}\n{producer_result[:1000]}\n{critic_result[:1000]}".encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        return f"mem_{stage_id}_{digest}"

    def _update_q(self, record: dict[str, Any], reward_signal: float, alpha: float = 0.35) -> None:
        old_q = float(record.get("q_value", 0.0) or 0.0)
        new_q = _clamp((1.0 - alpha) * old_q + alpha * float(reward_signal))
        old_ma = float(record.get("reward_ma", 0.0) or 0.0)
        record["q_value"] = new_q
        record["q_visits"] = int(record.get("q_visits", 0) or 0) + 1
        record["reward_ma"] = _clamp((1.0 - alpha) * old_ma + alpha * float(reward_signal))
