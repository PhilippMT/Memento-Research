"""Tests for promote_to_product tool."""

import os
import subprocess
from unittest.mock import patch

import pytest

from onemancompany.core import product_workspace as pw


def _git_cmd(args: list[str], cwd) -> None:
    """Run git without inheriting GIT_* env vars (same as product_workspace._git)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def product_workspace(tmp_path):
    ws_dir = tmp_path / "workspace"
    pw.init_workspace(ws_dir)
    wt_path = tmp_path / "product_worktree"
    pw.add_worktree(ws_dir, wt_path, "proj_test")
    return {"workspace_dir": ws_dir, "worktree_path": wt_path, "project_id": "proj_test"}


class TestPromoteToProductTool:
    @pytest.fixture(autouse=True)
    def _setup(self, product_workspace, monkeypatch):
        self.ws = product_workspace
        monkeypatch.setattr(
            "onemancompany.agents.product_workspace_tools._resolve_product_workspace",
            lambda: (self.ws["workspace_dir"], self.ws["worktree_path"], self.ws["project_id"]),
        )

    @pytest.mark.asyncio
    async def test_promote_clean(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        wt = self.ws["worktree_path"]
        (wt / "output.md").write_text("# Output\n")
        _git_cmd(["add", "."], cwd=wt)
        _git_cmd(["commit", "-m", "add output"], cwd=wt)
        result = await promote_to_product.ainvoke({})
        assert "merged" in result.lower() or "promoted" in result.lower()

    @pytest.mark.asyncio
    async def test_promote_no_changes(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        result = await promote_to_product.ainvoke({})
        assert "nothing" in result.lower() or "no change" in result.lower()

    @pytest.mark.asyncio
    async def test_promote_abort(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        ws_dir = self.ws["workspace_dir"]
        wt_path = self.ws["worktree_path"]

        # Create conflicting changes on main
        (ws_dir / "README.md").write_text("# main edit\n")
        _git_cmd(["add", "."], cwd=ws_dir)
        _git_cmd(["commit", "-m", "main"], cwd=ws_dir)

        # Create conflicting changes on project branch
        (wt_path / "README.md").write_text("# project edit\n")
        _git_cmd(["add", "."], cwd=wt_path)
        _git_cmd(["commit", "-m", "project"], cwd=wt_path)

        # First call triggers conflict
        await promote_to_product.ainvoke({})

        # Abort the merge
        result = await promote_to_product.ainvoke({"abort": True})
        assert "abort" in result.lower()

    @pytest.mark.asyncio
    async def test_promote_conflict_formats_versions(self, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt

        monkeypatch.setattr(
            pwt.pw,
            "promote",
            lambda *args, **kwargs: {
                "status": "conflict",
                "message": "conflict",
                "conflicts": [
                    {
                        "file": "README.md",
                        "your_version": "project text",
                        "product_version": "product text",
                    }
                ],
            },
        )

        result = await pwt.promote_to_product.ainvoke({})

        assert "Merge conflicts detected in 1 file" in result
        assert "--- README.md ---" in result
        assert "YOUR VERSION:\nproject text" in result
        assert "PRODUCT VERSION:\nproduct text" in result


class TestResolveProductWorkspace:
    def test_no_active_vessel(self):
        from onemancompany.agents.product_workspace_tools import _resolve_product_workspace
        from onemancompany.core.vessel import _current_vessel

        token = _current_vessel.set(None)
        try:
            with pytest.raises(ValueError, match="No active vessel"):
                _resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_missing_project_id(self):
        from onemancompany.agents.product_workspace_tools import _resolve_product_workspace
        from onemancompany.core.vessel import _current_vessel

        token = _current_vessel.set(type("Vessel", (), {"_running_node": None})())
        try:
            with pytest.raises(ValueError, match="not part of a project"):
                _resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_project_not_found(self, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt
        from onemancompany.core.vessel import _current_vessel

        monkeypatch.setattr("onemancompany.core.project_archive.load_named_project", lambda project_id: None)
        vessel = type("Vessel", (), {"_running_node": type("Node", (), {"project_id": "p1"})()})()
        token = _current_vessel.set(vessel)
        try:
            with pytest.raises(ValueError, match="Project p1 not found"):
                pwt._resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_project_without_product(self, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt
        from onemancompany.core.vessel import _current_vessel

        monkeypatch.setattr("onemancompany.core.project_archive.load_named_project", lambda project_id: {"id": project_id})
        vessel = type("Vessel", (), {"_running_node": type("Node", (), {"project_id": "p1"})()})()
        token = _current_vessel.set(vessel)
        try:
            with pytest.raises(ValueError, match="not linked to a product"):
                pwt._resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_missing_product_slug(self, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt
        from onemancompany.core.vessel import _current_vessel

        monkeypatch.setattr("onemancompany.core.project_archive.load_named_project", lambda project_id: {"product_id": "prod1"})
        monkeypatch.setattr("onemancompany.core.product.find_slug_by_product_id", lambda product_id: "")
        vessel = type("Vessel", (), {"_running_node": type("Node", (), {"project_id": "p1"})()})()
        token = _current_vessel.set(vessel)
        try:
            with pytest.raises(ValueError, match="Product not found"):
                pwt._resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_missing_workspace_or_worktree(self, tmp_path, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt
        from onemancompany.core.vessel import _current_vessel

        monkeypatch.setattr("onemancompany.core.project_archive.load_named_project", lambda project_id: {"product_id": "prod1"})
        monkeypatch.setattr("onemancompany.core.product.find_slug_by_product_id", lambda product_id: "prod-slug")
        monkeypatch.setattr("onemancompany.core.config.PRODUCTS_DIR", tmp_path / "products")
        monkeypatch.setattr("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects")
        vessel = type("Vessel", (), {"_running_node": type("Node", (), {"project_id": "p1"})()})()
        token = _current_vessel.set(vessel)
        try:
            with pytest.raises(ValueError, match="workspace not initialized"):
                pwt._resolve_product_workspace()

            (tmp_path / "products" / "prod-slug" / "workspace").mkdir(parents=True)
            with pytest.raises(ValueError, match="worktree not found"):
                pwt._resolve_product_workspace()
        finally:
            _current_vessel.reset(token)

    def test_resolves_workspace(self, tmp_path, monkeypatch):
        from onemancompany.agents import product_workspace_tools as pwt
        from onemancompany.core.vessel import _current_vessel

        workspace = tmp_path / "products" / "prod-slug" / "workspace"
        worktree = tmp_path / "projects" / "p1" / "product_worktree"
        workspace.mkdir(parents=True)
        worktree.mkdir(parents=True)
        monkeypatch.setattr("onemancompany.core.project_archive.load_named_project", lambda project_id: {"product_id": "prod1"})
        monkeypatch.setattr("onemancompany.core.product.find_slug_by_product_id", lambda product_id: "prod-slug")
        monkeypatch.setattr("onemancompany.core.config.PRODUCTS_DIR", tmp_path / "products")
        monkeypatch.setattr("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects")
        vessel = type("Vessel", (), {"_running_node": type("Node", (), {"project_id": "p1"})()})()
        token = _current_vessel.set(vessel)
        try:
            assert pwt._resolve_product_workspace() == (workspace, worktree, "p1")
        finally:
            _current_vessel.reset(token)
