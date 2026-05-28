from __future__ import annotations

import json

from onemancompany.core.research_memory import ResearchMemoryStore


def _stage(stage_id: int = 4) -> dict:
    return {
        "id": stage_id,
        "name": "Methodology Design",
        "skill": "methodology_designer",
    }


def _records(store: ResearchMemoryStore) -> list[dict]:
    if not store.memory_path.exists():
        return []
    return [json.loads(line) for line in store.memory_path.read_text(encoding="utf-8").splitlines()]


def test_retrieve_stage_guidance_ranks_by_similarity_and_q_value(tmp_path):
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    stage = _stage()

    good_id = store.record_stage_episode(
        topic="graph RAG evaluation",
        stage=stage,
        producer_result="Define a retrieval ablation with baselines, loss, metrics, and failure modes.",
        critic_result="PASS confidence: 0.92. The methodology is concrete and testable.",
        passed=True,
        confidence=0.92,
        retries=0,
        reward=0.92,
        outcome="critic_pass",
    )
    bad_id = store.record_stage_episode(
        topic="graph RAG evaluation",
        stage=stage,
        producer_result="Discuss graph RAG broadly without baselines or evaluation details.",
        critic_result="REJECT confidence: 0.28. Missing baselines and measurable claims.",
        passed=False,
        confidence=0.28,
        retries=0,
        reward=-0.72,
        outcome="critic_reject_retry",
    )

    retrieved = store.retrieve_stage_guidance(
        topic="graph RAG evaluation",
        stage=stage,
        context="Need methodology design with retrieval ablation, baselines, metrics, and losses.",
        k=2,
        threshold=0.0,
    )

    assert retrieved.memory_ids[0] == good_id
    assert bad_id in retrieved.memory_ids
    assert "Useful prior memories" in retrieved.guidance
    assert "Negative memories to avoid" in retrieved.guidance
    assert retrieved.simmax > 0


def test_apply_ceo_feedback_updates_episode_and_related_memory_q(tmp_path):
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    stage = _stage()
    related_id = store.record_stage_episode(
        topic="graph RAG evaluation",
        stage=stage,
        producer_result="Use concrete ablations and baselines.",
        critic_result="PASS confidence: 0.8",
        passed=True,
        confidence=0.8,
        retries=0,
        reward=0.8,
        outcome="critic_pass",
    )
    episode_id = store.record_stage_episode(
        topic="graph RAG evaluation",
        stage=stage,
        producer_result="New method draft still misses dataset details.",
        critic_result="PASS confidence: 0.6",
        passed=True,
        confidence=0.6,
        retries=1,
        reward=0.45,
        retrieved_memory_ids=[related_id],
        outcome="critic_pass",
    )

    update = store.apply_ceo_feedback(
        episode_id=episode_id,
        retrieved_memory_ids=[related_id],
        feedback="please REVISE with dataset and baseline details",
        approved=False,
    )
    records = {record["id"]: record for record in _records(store)}

    assert set(update["updated_ids"]) == {episode_id, related_id}
    assert records[episode_id]["ceo_approved"] is False
    assert "dataset" in records[episode_id]["ceo_feedback"]
    assert records[episode_id]["reward"] < 0.45
    assert records[related_id]["q_value"] < 0.8
