"""Tests for follow-up routing in task_followup.

Routing contract (no EA orchestrator fallback):
    1. Active pipeline + phase == gate → route to engine.on_ceo_approve(feedback)
    2. Active pipeline + phase != gate → engine.queue_pending_feedback(feedback)
    3. No pipeline + product owner exists → dispatch to product owner
    4. No pipeline + no owner → 400 error (no silent EA fallback)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from onemancompany.core.config import CEO_ID, EA_ID, TASK_TREE_FILENAME
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OWNER_ID = "00010"
PRODUCT_ID = "prod_abc"
PRODUCT_SLUG = "my-product"
PROJECT_ID = "proj_001"


def _make_project_doc(product_id: str = "") -> dict:
    return {
        "project_id": PROJECT_ID,
        "task": "Build feature X",
        "status": "completed",
        "product_id": product_id,
        "completed_at": "2026-01-01",
    }


def _make_tree_with_root(project_id: str = PROJECT_ID) -> TaskTree:
    """Create a minimal tree with CEO root + completed EA child."""
    tree = TaskTree(project_id=project_id)
    root = tree.create_root(employee_id=CEO_ID, description="Build feature X")
    root.node_type = NodeType.CEO_PROMPT
    root.set_status(TaskPhase.PROCESSING)
    ea_child = tree.add_child(
        parent_id=root.id,
        employee_id=EA_ID,
        description="Execute feature X",
        acceptance_criteria=[],
    )
    ea_child.set_status(TaskPhase.PROCESSING)
    ea_child.set_status(TaskPhase.COMPLETED)
    return tree


def _mock_product(product_id: str = PRODUCT_ID, owner_id: str = OWNER_ID) -> dict:
    return {
        "id": product_id,
        "slug": PRODUCT_SLUG,
        "name": "My Product",
        "owner_id": owner_id,
        "status": "active",
    }


# ---------------------------------------------------------------------------
# Bug 1: task_followup should route to product owner for product-linked projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_followup_routes_to_product_owner_when_product_linked(tmp_path):
    """When a project is linked to a product with an owner, the followup
    node should be assigned to the product owner, NOT the EA."""
    from onemancompany.api.routes import task_followup

    # Set up project dir with tree file present so tree_path.exists() is True
    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree = _make_tree_with_root()
    tree_path = pdir / TASK_TREE_FILENAME
    tree_path.write_text("{}")  # dummy content, get_tree is mocked

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()
    mock_em._schedule_next = MagicMock()

    mock_agent_loop = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=None), \
         patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
         patch("onemancompany.core.vessel._save_project_tree"), \
         patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_agent_loop), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.core.project_archive._save_resolved"), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=PRODUCT_SLUG), \
         patch("onemancompany.core.product.load_product", return_value=_mock_product()):

        result = await task_followup(PROJECT_ID, {"instructions": "Update the KR progress"})

    assert result["status"] == "ok"

    # The key assertion: schedule_node should be called with the product OWNER,
    # not the EA.
    scheduled_employee_id = mock_em.schedule_node.call_args[0][0]
    assert scheduled_employee_id == OWNER_ID, (
        f"Expected followup to be routed to product owner {OWNER_ID}, "
        f"but was routed to {scheduled_employee_id}"
    )


@pytest.mark.asyncio
async def test_task_followup_errors_when_no_pipeline_and_no_product(tmp_path):
    """No pipeline + no product owner → 400 error. The legacy EA-fallback path
    is gone — followup must have either an orchestrator (pipeline) or a human
    owner (product) to route to."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    tree_path = pdir / TASK_TREE_FILENAME
    tree_path.write_text("{}")

    project_doc = _make_project_doc(product_id="")  # no product

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=None), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        with pytest.raises(HTTPException) as exc_info:
            await task_followup(PROJECT_ID, {"instructions": "Check status"})

    assert exc_info.value.status_code == 400
    assert "pipeline" in exc_info.value.detail.lower() or "owner" in exc_info.value.detail.lower()
    mock_em.schedule_node.assert_not_called()


@pytest.mark.asyncio
async def test_task_followup_errors_when_product_has_no_owner(tmp_path):
    """Product without owner → 400 error (no EA fallback)."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    no_owner_product = _mock_product()
    no_owner_product["owner_id"] = ""

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=None), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=PRODUCT_SLUG), \
         patch("onemancompany.core.product.load_product", return_value=no_owner_product), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        with pytest.raises(HTTPException) as exc_info:
            await task_followup(PROJECT_ID, {"instructions": "Review progress"})

    assert exc_info.value.status_code == 400
    mock_em.schedule_node.assert_not_called()


@pytest.mark.asyncio
async def test_task_followup_errors_when_product_slug_not_found(tmp_path):
    """Product slug lookup fails (deleted product) → 400 error (no EA fallback)."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id=PRODUCT_ID)

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=None), \
         patch("onemancompany.core.product.find_slug_by_product_id", return_value=None), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        with pytest.raises(HTTPException) as exc_info:
            await task_followup(PROJECT_ID, {"instructions": "Check KRs"})

    assert exc_info.value.status_code == 400
    mock_em.schedule_node.assert_not_called()


# ---------------------------------------------------------------------------
# Active-pipeline routing (replaces the legacy EA-orchestrator path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_followup_routes_to_pipeline_gate_when_gate_open(tmp_path):
    """Active pipeline at phase='gate' → call engine.on_ceo_approve(feedback)
    and do not create any task-tree dispatch."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id="")

    mock_engine = MagicMock()
    mock_engine.phase = "gate"
    mock_engine.current_stage = 4
    mock_engine.state = {"phase": "gate"}
    mock_engine.on_ceo_approve = MagicMock()

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        result = await task_followup(PROJECT_ID, {"instructions": "按意见整改"})

    mock_engine.on_ceo_approve.assert_called_once()
    assert mock_engine.on_ceo_approve.call_args.kwargs.get("feedback") == "按意见整改" \
        or mock_engine.on_ceo_approve.call_args.args == ("按意见整改",)
    mock_em.schedule_node.assert_not_called()
    assert result["routed_to"] == "pipeline_gate"
    assert result["stage"] == 4


@pytest.mark.asyncio
async def test_task_followup_queues_feedback_when_pipeline_mid_flight(tmp_path):
    """Active pipeline at phase!='gate' (producer/critic running, auto-retry) →
    queue feedback on the engine for the next dispatch. Do not dispatch to EA
    and do not call on_ceo_approve."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id="")

    mock_engine = MagicMock()
    mock_engine.phase = "producer"  # mid-retry
    mock_engine.current_stage = 4
    mock_engine.state = {"phase": "producer"}
    mock_engine.queue_pending_feedback = MagicMock()
    mock_engine.on_ceo_approve = MagicMock()

    mock_em = MagicMock()
    mock_em.schedule_node = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine), \
         patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        result = await task_followup(PROJECT_ID, {"instructions": "按意见整改"})

    mock_engine.queue_pending_feedback.assert_called_once_with("按意见整改")
    mock_engine.on_ceo_approve.assert_not_called()
    mock_em.schedule_node.assert_not_called()
    assert result["routed_to"] == "pipeline_queue"
    assert result["stage"] == 4


@pytest.mark.parametrize("terminal_phase", ["done", "failed"])
@pytest.mark.asyncio
async def test_task_followup_skips_pipeline_when_phase_terminal(tmp_path, terminal_phase):
    """A pipeline that has phase in ('done','failed') is not an active
    orchestrator — feedback cannot reach a producer. Followup must fall
    through to the product-owner path (or 400 if no owner). 'failed' is
    explicitly covered so feedback into a dead pipeline can't silently
    accumulate in pending_user_feedback."""
    from onemancompany.api.routes import task_followup

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id="")  # no product either

    mock_engine = MagicMock()
    mock_engine.phase = terminal_phase
    mock_engine.current_stage = 4 if terminal_phase == "failed" else 9
    mock_engine.on_ceo_approve = MagicMock()
    mock_engine.queue_pending_feedback = MagicMock()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        with pytest.raises(HTTPException) as exc_info:
            await task_followup(PROJECT_ID, {"instructions": "Check status"})

    assert exc_info.value.status_code == 400
    mock_engine.on_ceo_approve.assert_not_called()
    mock_engine.queue_pending_feedback.assert_not_called()


# ---------------------------------------------------------------------------
# Integration-flavored test — uses a real PipelineEngine + state file so the
# routing checks against the actual ``phase`` property / state-file persistence
# rather than a ``MagicMock``. This catches regressions where the mock-based
# tests pass but the real engine behaves differently (e.g. property vs attr).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_followup_with_real_engine_persists_pending_feedback(tmp_path):
    """End-to-end with a real PipelineEngine: queue path persists the
    feedback into state['pending_user_feedback'] on the YAML state file."""
    from onemancompany.api.routes import task_followup
    from onemancompany.core import pipeline_engine as pe

    pdir = tmp_path / PROJECT_ID
    pdir.mkdir()
    (pdir / TASK_TREE_FILENAME).write_text("{}")

    project_doc = _make_project_doc(product_id="")

    # Build a real engine, seed its state to "mid-flight retry", persist.
    engine = pe.PipelineEngine(PROJECT_ID, str(pdir), "topic")
    engine.state["current_stage"] = 4
    engine.state["phase"] = "producer"
    engine.state["retries"] = 1
    engine._save()

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(pdir)), \
         patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v1", project_doc, "key1")), \
         patch("onemancompany.core.project_archive.append_action"), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=engine), \
         patch("onemancompany.api.routes.event_bus", AsyncMock()):

        result = await task_followup(PROJECT_ID, {"instructions": "按意见整改"})

    # Routed via the queue path (mid-flight).
    assert result["routed_to"] == "pipeline_queue"
    # Real engine state mutated AND persisted.
    assert engine.state["pending_user_feedback"] == "按意见整改"
    reloaded = pe._load_state(str(pdir))
    assert reloaded["pending_user_feedback"] == "按意见整改"
    # Cleanup the registry so it doesn't leak into other tests.
    pe._active_pipelines.pop(PROJECT_ID, None)
