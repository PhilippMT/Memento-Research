"""Tests for project_repo — git operations on a project workspace.

The pipeline_engine treats each project's workspace dir as a git repo:
  - ``ensure_initialized`` runs git init + baseline commit + ``iter001/init`` tag
    on first call (idempotent on subsequent calls).
  - ``commit_stage`` commits whatever's in the workspace and tags it
    ``iter001/stage-N`` after a stage's critic PASS.
  - ``checkout_branch_from_stage`` creates a new ``feat-stage<N>-<id>`` branch
    rooted at the previous stage's commit, used by the "revert to here"
    feature so the user can give new instructions for stage N and re-run
    forward.

All git ops shell out to the user's git binary via subprocess; the module
is intentionally thin (no GitPython, no caching).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from onemancompany.core import project_repo as pr


def _git(repo: Path, *args: str) -> str:
    """Helper: run git in repo, return stdout."""
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
    ).strip()


# ---------------------------------------------------------------------------
# ensure_initialized
# ---------------------------------------------------------------------------


def test_ensure_initialized_creates_repo_and_baseline_tag(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")

    assert (tmp_path / ".git").is_dir(), "git init should create .git"
    tags = _git(tmp_path, "tag", "--list").splitlines()
    assert "iter001/init" in tags

    # Initial commit exists and is reachable from HEAD
    head_sha = _git(tmp_path, "rev-parse", "HEAD")
    init_sha = _git(tmp_path, "rev-parse", "iter001/init")
    assert head_sha == init_sha


def test_ensure_initialized_idempotent(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    head_before = _git(tmp_path, "rev-parse", "HEAD")

    pr.ensure_initialized(str(tmp_path), iteration="iter001")  # second call no-op
    head_after = _git(tmp_path, "rev-parse", "HEAD")

    assert head_before == head_after, "second ensure_initialized must not mint a new commit"


def test_ensure_initialized_picks_up_existing_workspace_content(tmp_path):
    (tmp_path / "iterations").mkdir()
    (tmp_path / "iterations" / "iter_001").mkdir()
    (tmp_path / "iterations" / "iter_001" / "pipeline_state.yaml").write_text("topic: x\n")

    pr.ensure_initialized(str(tmp_path), iteration="iter001")

    listed = _git(tmp_path, "ls-tree", "-r", "HEAD", "--name-only").splitlines()
    assert "iterations/iter_001/pipeline_state.yaml" in listed


# ---------------------------------------------------------------------------
# commit_stage
# ---------------------------------------------------------------------------


def test_commit_stage_creates_tagged_commit(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1_topic_refiner.md").write_text("stage 1 result\n")

    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Topic Refinement")

    tags = _git(tmp_path, "tag", "--list").splitlines()
    assert "iter001/stage-1" in tags
    head_msg = _git(tmp_path, "log", "-1", "--format=%s")
    assert "Stage 1" in head_msg
    listed = _git(tmp_path, "ls-tree", "-r", "HEAD", "--name-only").splitlines()
    assert "iterations/iter_001/stage1_topic_refiner.md" in listed


def test_commit_stage_no_op_when_clean(tmp_path):
    """Nothing changed in the workspace since last commit → no new commit
    (otherwise we'd produce empty commits on re-PASS after retry)."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    head_before = _git(tmp_path, "rev-parse", "HEAD")

    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Topic Refinement")

    head_after = _git(tmp_path, "rev-parse", "HEAD")
    tags = _git(tmp_path, "tag", "--list").splitlines()
    # Tag still gets created pointing at HEAD even if no new commit (so reverts work).
    assert "iter001/stage-1" in tags
    assert head_before == head_after


def test_commit_stage_overwrites_existing_tag(tmp_path):
    """Re-running a stage (after a revert) overwrites the existing
    ``iter001/stage-N`` tag with the new commit. Otherwise the old tag
    would stick around pointing at the original execution and confuse
    later revert ops."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1_topic_refiner.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Topic Refinement")
    first_tag_sha = _git(tmp_path, "rev-parse", "iter001/stage-1")

    # Second pass after revert: workspace shows a new version of the file.
    (iter_dir / "stage1_topic_refiner.md").write_text("v2\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Topic Refinement")

    second_tag_sha = _git(tmp_path, "rev-parse", "iter001/stage-1")
    assert first_tag_sha != second_tag_sha


# ---------------------------------------------------------------------------
# checkout_branch_from_stage — the "revert to here" primitive
# ---------------------------------------------------------------------------


def test_checkout_branch_from_stage_creates_branch_at_previous_tag(tmp_path):
    """Reverting to stage N means: re-do stage N. The branch must be rooted
    at the tag *before* stage N (so checkout doesn't include stage N's
    own commit), which is ``iter001/stage-<N-1>`` for N>=2 or
    ``iter001/init`` for N=1."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    for n in (1, 2, 3):
        (iter_dir / f"stage{n}.md").write_text(f"v{n}\n")
        pr.commit_stage(str(tmp_path), iteration="iter001", stage=n, stage_name=f"Stage {n}")

    branch_name = pr.checkout_branch_from_stage(
        str(tmp_path), iteration="iter001", stage=2,
    )

    assert branch_name.startswith("feat-stage2-"), branch_name
    current = _git(tmp_path, "branch", "--show-current")
    assert current == branch_name

    # Workspace now reflects the state from stage 1's commit (one before stage 2).
    assert (iter_dir / "stage1.md").exists()
    assert not (iter_dir / "stage2.md").exists()
    assert not (iter_dir / "stage3.md").exists()


def test_checkout_branch_from_stage_1_roots_at_init(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")

    branch_name = pr.checkout_branch_from_stage(
        str(tmp_path), iteration="iter001", stage=1,
    )

    assert branch_name.startswith("feat-stage1-")
    # Workspace is back to the init state — no stage 1 file.
    assert not (iter_dir / "stage1.md").exists()


def test_checkout_branch_from_stage_explicit_branch_name(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")

    branch_name = pr.checkout_branch_from_stage(
        str(tmp_path), iteration="iter001", stage=1, branch_name="my-redo",
    )

    assert branch_name == "my-redo"
    assert _git(tmp_path, "branch", "--show-current") == "my-redo"


def test_discard_uncommitted_changes_drops_tracked_and_untracked(tmp_path):
    """Used by revert-mid-flight: cancelled producer's partial writes must
    be scrubbed before checkout, otherwise DirtyWorkspaceError fires."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("committed\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")

    # Simulate a cancelled producer mid-write: modify a tracked file and
    # also leave an untracked scratch file behind.
    (iter_dir / "stage1.md").write_text("partial dirty\n")
    (iter_dir / "scratch.tmp").write_text("garbage\n")
    assert pr._has_uncommitted_changes(str(tmp_path))

    pr.discard_uncommitted_changes(str(tmp_path))

    assert not pr._has_uncommitted_changes(str(tmp_path))
    assert (iter_dir / "stage1.md").read_text() == "committed\n"
    assert not (iter_dir / "scratch.tmp").exists()


def test_discard_uncommitted_changes_noop_on_uninitialized_repo(tmp_path):
    """Defensive: called on a non-repo directory should not raise."""
    pr.discard_uncommitted_changes(str(tmp_path))  # no .git/ — should silently no-op


def test_checkout_branch_from_stage_refuses_dirty_worktree(tmp_path):
    """If the user's workspace has uncommitted changes (which would be a
    bug — the engine should only revert at idle moments), refuse rather
    than silently discard the changes."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")

    # Introduce an uncommitted modification.
    (iter_dir / "stage1.md").write_text("dirty\n")

    with pytest.raises(pr.DirtyWorkspaceError):
        pr.checkout_branch_from_stage(str(tmp_path), iteration="iter001", stage=1)


def test_checkout_branch_from_stage_unknown_stage(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")

    with pytest.raises(pr.StageNotCommittedError):
        pr.checkout_branch_from_stage(str(tmp_path), iteration="iter001", stage=5)


@pytest.mark.parametrize("malicious_name", [
    "--orphan",          # leading dash → would be parsed as a git flag
    "-q",
    "..",                # refname rule violation
    "foo..bar",          # also refname violation
    "foo/",              # trailing slash
    "foo.lock",          # reserved suffix
    "foo bar",           # space
    "foo;rm",            # shell-ish injection
    "/leading-slash",
    # Empty string is intentionally NOT in this list — the function
    # treats ``branch_name=""`` as "use default" (``or _gen_branch_name``),
    # which is a UX nicety distinct from "user supplied an unsafe value".
])
def test_checkout_branch_from_stage_rejects_unsafe_branch_names(tmp_path, malicious_name):
    """Critical defence: branch names flow unvalidated from the HTTP body
    to ``git checkout -b <name>``. Names starting with ``-`` or containing
    refname-rule violations must be rejected before git sees them."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")

    with pytest.raises(pr.InvalidBranchNameError):
        pr.checkout_branch_from_stage(
            str(tmp_path), iteration="iter001", stage=1, branch_name=malicious_name,
        )


def test_ensure_initialized_writes_gitignore(tmp_path):
    """The wide ``git add -A`` in commit_stage would otherwise capture
    ``.DS_Store``, swap files, ``__pycache__``, etc. The default
    .gitignore prevents that."""
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    gi = tmp_path / ".gitignore"
    assert gi.exists()
    content = gi.read_text()
    assert ".DS_Store" in content
    assert "__pycache__" in content


def test_ensure_initialized_works_when_initial_branch_flag_unsupported(tmp_path, monkeypatch):
    """git < 2.28 rejects ``--initial-branch=main``. The fallback runs
    plain ``git init`` then ``symbolic-ref HEAD refs/heads/main`` so the
    repo still ends up on ``main``."""
    real_run = pr._run
    seen = []

    def fake_run(repo_dir, *args, check=True):
        # Simulate "old git" rejecting --initial-branch.
        if args[:2] == ("init", "--quiet") and "--initial-branch=main" in args:
            seen.append("rejected")
            cp = subprocess.CompletedProcess(args=args, returncode=128, stdout="", stderr="unknown option")
            return cp
        return real_run(repo_dir, *args, check=check)

    monkeypatch.setattr(pr, "_run", fake_run)
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    assert "rejected" in seen, "test setup must have triggered the fallback path"
    # Repo still works.
    assert (tmp_path / ".git").is_dir()
    tags = _git(tmp_path, "tag", "--list").splitlines()
    assert "iter001/init" in tags


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def test_current_branch_returns_active_branch(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    # Default branch name varies by git version (main vs master); the
    # function should return whatever is checked out.
    name = pr.current_branch(str(tmp_path))
    assert name in ("main", "master")


def test_list_branches_returns_all(tmp_path):
    pr.ensure_initialized(str(tmp_path), iteration="iter001")
    iter_dir = tmp_path / "iterations" / "iter_001"
    iter_dir.mkdir(parents=True)
    (iter_dir / "stage1.md").write_text("v1\n")
    pr.commit_stage(str(tmp_path), iteration="iter001", stage=1, stage_name="Stage 1")
    pr.checkout_branch_from_stage(str(tmp_path), iteration="iter001", stage=1, branch_name="feat-redo-1")

    branches = pr.list_branches(str(tmp_path))
    names = {b["name"] for b in branches}
    assert "feat-redo-1" in names
    assert any(b in names for b in ("main", "master"))

    feat = next(b for b in branches if b["name"] == "feat-redo-1")
    assert feat["current"] is True
    assert "head_commit" in feat and len(feat["head_commit"]) >= 7
