"""Targeted coverage for ``onemancompany.core.research_memory`` edge branches:
text truncation, cosine zero-vector returns, default-root resolution, JSONL
parse error skip, and apply_ceo_feedback's empty-store fast path."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from onemancompany.core import research_memory as rm
from onemancompany.core.research_memory import ResearchMemoryStore


def _stage(stage_id: int = 4) -> dict:
    return {"id": stage_id, "name": "Methodology Design", "skill": "methodology_designer"}


def test_limit_text_truncates_above_limit():
    long = "x" * (rm.MAX_STORED_TEXT + 100)
    out = rm._limit_text(long)
    assert out.endswith("...[truncated]")
    assert len(out) <= rm.MAX_STORED_TEXT


def test_snippet_truncates_above_limit():
    long = "word " * (rm.MAX_SNIPPET_TEXT)
    out = rm._snippet(long)
    assert out.endswith("...")
    assert len(out) <= rm.MAX_SNIPPET_TEXT


def test_cosine_similarity_returns_zero_for_empty_vector():
    assert rm._cosine_similarity(Counter(), Counter({"a": 1})) == 0.0


def test_cosine_similarity_returns_zero_when_norm_is_zero():
    # Both sides non-empty in keys, but one side has zero counts → zero norm.
    assert rm._cosine_similarity(Counter({"a": 0}), Counter({"a": 1})) == 0.0


def test_default_memory_root_falls_back_outside_projects_dir(tmp_path: Path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    root = rm._default_memory_root(outside)
    assert root == outside / f".{rm.MEMORY_DIR_NAME}"


def test_default_memory_root_uses_global_dir_for_pipeline_projects(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rm, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(rm, "DATA_ROOT", tmp_path / "data")
    inside = tmp_path / "proj-1"
    inside.mkdir()
    assert rm._default_memory_root(inside) == tmp_path / "data" / rm.MEMORY_DIR_NAME


def test_read_records_skips_blank_and_corrupt_lines(tmp_path: Path):
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    store.memory_dir.mkdir(parents=True, exist_ok=True)
    # Mix of blank lines, malformed json, and a valid record.
    store.memory_path.write_text(
        '\n   \n{not json\n{"id":"keep","stage_id":1}\n',
        encoding="utf-8",
    )
    records = store._read_records()
    assert [r["id"] for r in records] == ["keep"]


def test_read_records_swallows_oserror(tmp_path: Path, monkeypatch):
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    store.memory_dir.mkdir(parents=True, exist_ok=True)
    store.memory_path.write_text("{}\n", encoding="utf-8")

    class BoomPath:
        def __init__(self, real: Path):
            self._real = real

        def exists(self):
            return True

        def open(self, *a, **kw):
            raise OSError("disk gone")

    monkeypatch.setattr(store, "memory_path", BoomPath(store.memory_path))
    assert store._read_records() == []


def test_apply_ceo_feedback_returns_early_when_store_is_empty(tmp_path: Path):
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    out = store.apply_ceo_feedback(
        episode_id="missing", retrieved_memory_ids=[], approved=True
    )
    assert out == {"episode_id": "missing", "updated_ids": []}


def test_retrieve_stage_guidance_drops_candidates_below_threshold(tmp_path: Path):
    """Boost the threshold so no candidate passes — the ranking loop's
    similarity-continue branch is exercised and the empty-candidate
    early return fires."""
    store = ResearchMemoryStore("p1", tmp_path, memory_root=tmp_path / "memory")
    store.record_stage_episode(
        topic="aaaa",
        stage=_stage(stage_id=1),
        producer_result="aaaa",
        critic_result="aaaa",
        passed=True,
        confidence=0.5,
        retries=0,
        reward=0.5,
    )
    out = store.retrieve_stage_guidance(
        topic="zzzz", stage=_stage(stage_id=4), context="zzzz",
        threshold=10.0,  # unreachable similarity → every candidate dropped
    )
    assert out.guidance == "" and out.memory_ids == []
