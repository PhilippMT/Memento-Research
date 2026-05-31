"""Startup watchdog: detect pipelines whose ``active_node_id`` points at
a task tree node that has already resolved on disk, and synthesise the
``on_task_complete`` / ``on_task_failed`` event the live event loop
already lost.

Background: a real production stall (issue #82) saw
``pipeline_state.yaml`` stuck at ``current_stage: 4, phase: producer,
active_node_id: <NODE>`` for 4+ hours while the corresponding task tree
node was FINISHED. The completion event vanished into a 60 s completion-
consumer timeout. No code re-evaluated, all 13 agents went idle, the
only recovery was hand-editing the YAML.

This watchdog runs once during lifespan startup, scans every
``iter_*/pipeline_state.yaml``, and for any project whose active node
has resolved on disk, replays the missing event into the pipeline
engine. Single-shot, idempotent — calling it again finds nothing to do."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers — build a minimal on-disk project / pipeline / task tree triple
# the watchdog will see.
# ---------------------------------------------------------------------------

def _make_iter(tmp_path: Path, *, pipeline_state: dict, node_status: str,
               node_id: str = "node-stage-4", employee_id: str = "00009",
               node_result: str = "stage 4 producer output") -> Path:
    """Lay out one project iteration on disk so the watchdog can find it.

    Returns the iteration dir."""
    proj = tmp_path / "proj-1"
    iter_dir = proj / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    # pipeline_state.yaml — what the engine has.
    (iter_dir / "pipeline_state.yaml").write_text(
        yaml.safe_dump(pipeline_state, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    # task_tree.yaml — what actually happened.
    tree = {
        "project_id": "proj-1",
        "nodes": {
            node_id: {
                "id": node_id,
                "parent_id": "",
                "children_ids": [],
                "employee_id": employee_id,
                "title": "Stage producer",
                "description": "synthetic",
                "node_type": "task",
                "status": node_status,
                "result": node_result,
            },
        },
    }
    (iter_dir / "task_tree.yaml").write_text(
        yaml.safe_dump(tree, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return iter_dir


# ---------------------------------------------------------------------------
# 1. Resolved producer node → replay on_task_complete.
# ---------------------------------------------------------------------------

class TestActiveNodeResolved:
    def test_completed_node_triggers_on_task_complete(self, tmp_path, monkeypatch):
        """The reported stall: pipeline state still says ``phase: producer
        active_node_id: X``, but X is COMPLETED in the tree. Watchdog must
        call ``engine.on_task_complete`` so the stage advances."""
        from onemancompany.core import pipeline_engine as pe

        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
                "phase": "producer", "active_node_id": "node-stage-4", "retries": 1,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status="completed",
        )

        # Capture which method the watchdog drives on the engine.
        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 1
        engine.on_task_complete.assert_called_once()
        args, kwargs = engine.on_task_complete.call_args
        # signature: (employee_id, node_id, result)
        emp, nid, result = args[0], args[1], args[2]
        assert emp == "00009"
        assert nid == "node-stage-4"
        assert result == "stage 4 producer output"
        engine.on_task_failed.assert_not_called()

    @pytest.mark.parametrize("status", ["accepted", "finished"])
    def test_other_resolved_states_also_complete(self, tmp_path, monkeypatch, status):
        """ACCEPTED / FINISHED nodes are equally "definitely done" — they
        all advance the stage via on_task_complete."""
        from onemancompany.core import pipeline_engine as pe

        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
                "phase": "producer", "active_node_id": "node-stage-4", "retries": 0,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status=status,
        )

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 1
        engine.on_task_complete.assert_called_once()
        engine.on_task_failed.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Failed / cancelled active node → replay on_task_failed.
# ---------------------------------------------------------------------------

class TestActiveNodeFailedOrCancelled:
    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    def test_failed_or_cancelled_node_triggers_on_task_failed(
            self, tmp_path, monkeypatch, status):
        """If the active node ended in FAILED / CANCELLED, the engine
        deserves to know — so its retry / fail-stage handler runs."""
        from onemancompany.core import pipeline_engine as pe

        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
                "phase": "producer", "active_node_id": "node-stage-4", "retries": 1,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status=status,
            node_result=f"stage 4 producer {status}: timeout",
        )

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 1
        engine.on_task_failed.assert_called_once()
        engine.on_task_complete.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Already-in-flight + missing-node + no-active-node → no-op.
# ---------------------------------------------------------------------------

class TestNoOpScenarios:
    def test_processing_node_left_alone(self, tmp_path, monkeypatch):
        """Active node is still PROCESSING — that's an in-flight task the
        watchdog must not interrupt. No recovery, no method call."""
        from onemancompany.core import pipeline_engine as pe

        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
                "phase": "producer", "active_node_id": "node-stage-4", "retries": 0,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status="processing",
        )

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 0
        engine.on_task_complete.assert_not_called()
        engine.on_task_failed.assert_not_called()

    def test_no_active_node_skipped(self, tmp_path, monkeypatch):
        """``active_node_id`` is None — pipeline is between dispatches.
        Nothing to recover."""
        from onemancompany.core import pipeline_engine as pe

        proj = tmp_path / "proj-1"
        iter_dir = proj / "iterations" / "iter_001"
        iter_dir.mkdir(parents=True)
        (iter_dir / "pipeline_state.yaml").write_text(yaml.safe_dump({
            "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
            "phase": "gate", "active_node_id": None, "retries": 0,
            "stage_results": {}, "stage_assignments": {},
        }), encoding="utf-8")

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 0
        engine.on_task_complete.assert_not_called()
        engine.on_task_failed.assert_not_called()

    @pytest.mark.parametrize("phase", ["done", "failed"])
    def test_terminal_phases_skipped(self, tmp_path, monkeypatch, phase):
        """Pipeline is already terminal — never re-fire events."""
        from onemancompany.core import pipeline_engine as pe

        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "x", "current_stage": 9, "start_stage": 1, "end_stage": 9,
                "phase": phase, "active_node_id": "node-x", "retries": 0,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status="finished",
        )

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)

        assert recovered == 0
        engine.on_task_complete.assert_not_called()

    def test_missing_task_tree_skipped_not_raised(self, tmp_path, monkeypatch):
        """``pipeline_state.yaml`` exists but the corresponding
        ``task_tree.yaml`` doesn't (corrupted state). Watchdog must log
        and move on, not crash the whole startup."""
        from onemancompany.core import pipeline_engine as pe

        proj = tmp_path / "proj-1"
        iter_dir = proj / "iterations" / "iter_001"
        iter_dir.mkdir(parents=True)
        (iter_dir / "pipeline_state.yaml").write_text(yaml.safe_dump({
            "topic": "x", "current_stage": 4, "start_stage": 1, "end_stage": 9,
            "phase": "producer", "active_node_id": "node-missing", "retries": 0,
            "stage_results": {}, "stage_assignments": {},
        }), encoding="utf-8")
        # NB: no task_tree.yaml on purpose.

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        # Must NOT raise — watchdog is best-effort.
        recovered = pe.recover_stalled_pipelines(tmp_path)
        assert recovered == 0


# ---------------------------------------------------------------------------
# 4. Walk semantics — multiple projects, only the stuck ones are recovered.
# ---------------------------------------------------------------------------

class TestMultiProjectWalk:
    def test_walks_all_projects_and_returns_count(self, tmp_path, monkeypatch):
        """Two projects: one stuck (producer + completed node), one
        actively in-flight (processing). Watchdog should recover only
        the stuck one and return 1."""
        from onemancompany.core import pipeline_engine as pe

        # Stuck.
        _make_iter(
            tmp_path,
            pipeline_state={
                "topic": "stuck", "current_stage": 4, "start_stage": 1, "end_stage": 9,
                "phase": "producer", "active_node_id": "n1", "retries": 0,
                "stage_results": {}, "stage_assignments": {},
            },
            node_status="completed",
            node_id="n1",
        )
        # Second project — fresh fixture in a different subdir.
        proj2 = tmp_path / "proj-2"
        iter2 = proj2 / "iterations" / "iter_001"
        iter2.mkdir(parents=True)
        (iter2 / "pipeline_state.yaml").write_text(yaml.safe_dump({
            "topic": "in-flight", "current_stage": 2, "start_stage": 1, "end_stage": 9,
            "phase": "producer", "active_node_id": "n2", "retries": 0,
            "stage_results": {}, "stage_assignments": {},
        }), encoding="utf-8")
        (iter2 / "task_tree.yaml").write_text(yaml.safe_dump({
            "project_id": "proj-2",
            "nodes": {"n2": {"id": "n2", "employee_id": "00007", "status": "processing", "result": ""}},
        }), encoding="utf-8")

        engine = MagicMock(spec=["on_task_complete", "on_task_failed"])
        monkeypatch.setattr(pe, "get_or_load_pipeline", lambda pid, pdir: engine)

        recovered = pe.recover_stalled_pipelines(tmp_path)
        assert recovered == 1
        engine.on_task_complete.assert_called_once()
