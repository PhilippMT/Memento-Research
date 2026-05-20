"""Tests for the revert-to-stage HTTP endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

PROJECT_ID = "proj_xyz"


@pytest.mark.asyncio
async def test_revert_endpoint_happy_path(tmp_path):
    from onemancompany.api.routes import revert_pipeline_to_stage

    mock_engine = MagicMock()
    mock_engine.current_stage = 3
    mock_engine.phase = "producer"
    mock_engine.revert_to_stage = AsyncMock(return_value="feat-stage3-abcdef")

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine):
        result = await revert_pipeline_to_stage(
            PROJECT_ID,
            {"stage": 3, "instructions": "use H2O baseline"},
        )

    mock_engine.revert_to_stage.assert_called_once()
    kwargs = mock_engine.revert_to_stage.call_args.kwargs
    assert kwargs["stage"] == 3
    assert kwargs["instructions"] == "use H2O baseline"
    assert result["status"] == "reverted"
    assert result["branch"] == "feat-stage3-abcdef"
    assert result["stage"] == 3


@pytest.mark.asyncio
async def test_revert_endpoint_missing_stage(tmp_path):
    from onemancompany.api.routes import revert_pipeline_to_stage
    with pytest.raises(HTTPException) as exc:
        await revert_pipeline_to_stage(PROJECT_ID, {"instructions": "x"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_revert_endpoint_missing_instructions(tmp_path):
    from onemancompany.api.routes import revert_pipeline_to_stage
    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)):
        with pytest.raises(HTTPException) as exc:
            await revert_pipeline_to_stage(PROJECT_ID, {"stage": 2, "instructions": "   "})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_revert_endpoint_no_active_pipeline(tmp_path):
    from onemancompany.api.routes import revert_pipeline_to_stage
    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await revert_pipeline_to_stage(PROJECT_ID, {"stage": 2, "instructions": "x"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_revert_endpoint_maps_revert_not_allowed_to_409(tmp_path):
    """RevertNotAllowedError still surfaces (e.g., no employee with the
    required skill) — the route maps it to 409."""
    from onemancompany.api.routes import revert_pipeline_to_stage
    from onemancompany.core.pipeline_engine import RevertNotAllowedError

    mock_engine = MagicMock()
    mock_engine.revert_to_stage = AsyncMock(side_effect=RevertNotAllowedError("no skill"))

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine):
        with pytest.raises(HTTPException) as exc:
            await revert_pipeline_to_stage(PROJECT_ID, {"stage": 2, "instructions": "x"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_revert_endpoint_stage_out_of_range(tmp_path):
    from onemancompany.api.routes import revert_pipeline_to_stage

    mock_engine = MagicMock()
    mock_engine.revert_to_stage = AsyncMock(side_effect=ValueError("stage must be in [1, 9]"))

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)), \
         patch("onemancompany.core.pipeline_engine.get_or_load_pipeline", return_value=mock_engine):
        with pytest.raises(HTTPException) as exc:
            await revert_pipeline_to_stage(PROJECT_ID, {"stage": 99, "instructions": "x"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_branches_endpoint(tmp_path):
    from onemancompany.api.routes import list_pipeline_branches

    with patch("onemancompany.core.project_archive.get_project_dir", return_value=str(tmp_path)), \
         patch("onemancompany.core.project_repo.current_branch", return_value="main"), \
         patch("onemancompany.core.project_repo.list_branches",
               return_value=[{"name": "main", "head_commit": "abc1234", "current": True}]):
        result = await list_pipeline_branches(PROJECT_ID)

    assert result["current"] == "main"
    assert len(result["branches"]) == 1
