"""Detection of stuck pipelines that the auto-recovery watchdog cannot fix.

``recover_stalled_pipelines`` only handles the case where the producer
node has *resolved on disk* (COMPLETED / FAILED / etc.) but the
in-memory engine missed the event. It cannot help when the producer
node is still in PROCESSING — to the watchdog, the producer "looks like
it's still working" even if nothing has progressed for hours.

For that residual case, ``detect_stuck_pipelines`` surfaces the project
to the user via a ``PIPELINE_STUCK`` event so they can intervene
(restart the agent, edit state, etc.) rather than discovering the
silent stall by accident hours later (issue #82, PR 3)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import yaml


def _make_iter(
    tmp_path: Path,
    *,
    pipeline_state: dict,
    node_status: str,
    node_id: str = "node-stage-4",
    state_mtime_offset: float = 0,
) -> Path:
    proj = tmp_path / "proj-stuck"
    iter_dir = proj / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    state_path = iter_dir / "pipeline_state.yaml"
    state_path.write_text(
        yaml.safe_dump(pipeline_state, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    tree = {
        "project_id": "proj-stuck",
        "nodes": {
            node_id: {
                "id": node_id,
                "parent_id": "",
                "children_ids": [],
                "employee_id": "00009",
                "title": "Stage producer",
                "description": "synthetic",
                "node_type": "task",
                "status": node_status,
                "result": "",
            },
        },
    }
    (iter_dir / "task_tree.yaml").write_text(
        yaml.safe_dump(tree, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    if state_mtime_offset:
        old = time.time() + state_mtime_offset
        os.utime(state_path, (old, old))
    return iter_dir


class TestThresholdConstantExists:
    def test_threshold_is_positive_seconds(self):
        from onemancompany.core.pipeline_engine import PIPELINE_STUCK_THRESHOLD_SECONDS
        assert isinstance(PIPELINE_STUCK_THRESHOLD_SECONDS, int)
        assert PIPELINE_STUCK_THRESHOLD_SECONDS > 0


class TestEmptyAndHealthyCases:
    def test_returns_empty_list_when_no_projects(self, tmp_path):
        from onemancompany.core.pipeline_engine import detect_stuck_pipelines
        assert detect_stuck_pipelines(tmp_path) == []

    def test_recent_state_not_flagged_stuck(self, tmp_path):
        """Recently updated pipeline_state.yaml — engine is actively
        progressing, not stuck."""
        from onemancompany.core.pipeline_engine import detect_stuck_pipelines
        _make_iter(
            tmp_path,
            pipeline_state={
                "current_stage": 4,
                "phase": "producer",
                "active_node_id": "node-stage-4",
            },
            node_status="processing",
            state_mtime_offset=-60,  # 1 minute ago
        )
        assert detect_stuck_pipelines(tmp_path) == []

    def test_no_active_node_not_flagged(self, tmp_path):
        from onemancompany.core.pipeline_engine import detect_stuck_pipelines
        _make_iter(
            tmp_path,
            pipeline_state={
                "current_stage": 4,
                "phase": "producer",
                "active_node_id": None,
            },
            node_status="processing",
            state_mtime_offset=-7200,
        )
        assert detect_stuck_pipelines(tmp_path) == []

    def test_terminal_phase_not_flagged(self, tmp_path):
        """A pipeline that has reached ``done`` is finished — not stuck."""
        from onemancompany.core.pipeline_engine import detect_stuck_pipelines
        _make_iter(
            tmp_path,
            pipeline_state={
                "current_stage": 9,
                "phase": "done",
                "active_node_id": "node-stage-4",
            },
            node_status="processing",
            state_mtime_offset=-7200,
        )
        assert detect_stuck_pipelines(tmp_path) == []


class TestStuckDetection:
    def test_stale_state_with_processing_node_is_stuck(self, tmp_path):
        from onemancompany.core.pipeline_engine import (
            detect_stuck_pipelines,
            PIPELINE_STUCK_THRESHOLD_SECONDS,
        )
        _make_iter(
            tmp_path,
            pipeline_state={
                "current_stage": 4,
                "phase": "producer",
                "active_node_id": "node-stage-4",
            },
            node_status="processing",
            state_mtime_offset=-(PIPELINE_STUCK_THRESHOLD_SECONDS + 60),
        )
        stuck = detect_stuck_pipelines(tmp_path)
        assert len(stuck) == 1
        entry = stuck[0]
        assert entry["project_id"] == "proj-stuck"
        assert entry["current_stage"] == 4
        assert entry["phase"] == "producer"
        assert entry["active_node_id"] == "node-stage-4"
        assert entry["stale_seconds"] >= PIPELINE_STUCK_THRESHOLD_SECONDS

    def test_resolved_node_not_flagged_handled_by_recover(self, tmp_path):
        """If the producer node has already resolved on disk,
        ``recover_stalled_pipelines`` will replay the event. The stuck
        detector must defer to it — flagging would emit a spurious
        PIPELINE_STUCK that gets resolved milliseconds later."""
        from onemancompany.core.pipeline_engine import (
            detect_stuck_pipelines,
            PIPELINE_STUCK_THRESHOLD_SECONDS,
        )
        for status in ("completed", "accepted", "finished", "failed", "cancelled"):
            tmp = tmp_path / status
            tmp.mkdir()
            _make_iter(
                tmp,
                pipeline_state={
                    "current_stage": 4,
                    "phase": "producer",
                    "active_node_id": "node-stage-4",
                },
                node_status=status,
                state_mtime_offset=-(PIPELINE_STUCK_THRESHOLD_SECONDS + 60),
            )
            assert detect_stuck_pipelines(tmp) == [], (
                f"node_status={status} should be left to recover_stalled_pipelines"
            )


class TestEventTypeAvailable:
    def test_pipeline_stuck_event_type_exists(self):
        from onemancompany.core.models import EventType
        assert hasattr(EventType, "PIPELINE_STUCK")
        assert EventType.PIPELINE_STUCK.value == "pipeline_stuck"
