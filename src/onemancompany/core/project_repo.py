"""project_repo.py — thin git wrapper for project workspaces.

Each project workspace ``projects/{pid}/`` is a real git repository. The
pipeline engine commits the workspace after every stage's critic PASS,
tagging the commit ``<iteration>/stage-<N>`` (e.g. ``iter001/stage-3``).
A user can then "revert to stage N" — create a feature branch rooted at
the tag *before* stage N, edit instructions, and re-run forward.

This module is intentionally thin: subprocess to the user's git binary,
no GitPython, no caching. Operations are idempotent where it makes
sense (init, ensure_initialized).
"""

from __future__ import annotations

import secrets
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

# Identity used for engine-driven commits. Pipeline commits aren't authored
# by a human, so we use a stable bot-style identity rather than picking up
# the host's global git config (which may not be set in CI / containers).
_BOT_NAME = "AutoResearch Pipeline"
_BOT_EMAIL = "pipeline@autoresearch.local"

# Tag prefix used for the project's empty baseline commit (created by
# ensure_initialized). Stage tags follow ``<iteration>/stage-<N>``.
_INIT_TAG_SUFFIX = "init"


class ProjectRepoError(Exception):
    """Base class for project_repo errors."""


class DirtyWorkspaceError(ProjectRepoError):
    """Raised when an operation requires a clean workspace but uncommitted
    changes are present."""


class StageNotCommittedError(ProjectRepoError):
    """Raised when reverting to a stage that was never committed (no tag)."""


def _run(repo_dir: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run ``git -C <repo_dir> <args>``.

    Captures stdout/stderr as text. ``check=True`` raises CalledProcessError
    on non-zero exit; callers pass ``check=False`` when they need to
    distinguish "command succeeded but said nothing" from "command failed".
    """
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _git_dir(repo_dir: str) -> Path:
    return Path(repo_dir) / ".git"


def _is_initialized(repo_dir: str) -> bool:
    return _git_dir(repo_dir).exists()


def _stage_tag(iteration: str, stage: int) -> str:
    return f"{iteration}/stage-{stage}"


def _init_tag(iteration: str) -> str:
    return f"{iteration}/{_INIT_TAG_SUFFIX}"


def _has_uncommitted_changes(repo_dir: str) -> bool:
    """True if ``git status --porcelain`` reports any modified/untracked files."""
    result = _run(repo_dir, "status", "--porcelain")
    return bool(result.stdout.strip())


def _tag_exists(repo_dir: str, tag: str) -> bool:
    result = _run(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False)
    return result.returncode == 0


def _ensure_path_exists(repo_dir: str) -> None:
    Path(repo_dir).mkdir(parents=True, exist_ok=True)


def ensure_initialized(repo_dir: str, *, iteration: str) -> None:
    """Idempotently make ``repo_dir`` a git repo with a baseline commit.

    On first call: ``git init`` + commit everything currently in the
    workspace (or a ``.gitkeep`` marker if empty) + tag ``<iteration>/init``.

    On subsequent calls: no-op. Existing workspaces (created before this
    feature) get auto-migrated on the next pipeline start.
    """
    _ensure_path_exists(repo_dir)
    if _is_initialized(repo_dir):
        return

    logger.info("[project_repo] Initializing git repo at {}", repo_dir)
    _run(repo_dir, "init", "--quiet", "--initial-branch=main")
    # The init --initial-branch flag is available on git 2.28+; if the
    # caller's git is older they end up with master, which is fine — we
    # don't hardcode the name elsewhere.
    _run(repo_dir, "config", "user.name", _BOT_NAME)
    _run(repo_dir, "config", "user.email", _BOT_EMAIL)
    _run(repo_dir, "config", "commit.gpgsign", "false")

    # Ensure there's at least one file to commit so the initial commit is
    # non-empty (git allow-empty exists but downstream tools sometimes get
    # confused by empty commits).
    if not _has_uncommitted_changes(repo_dir) and not any(Path(repo_dir).iterdir() if (p := Path(repo_dir)).exists() else []):
        (Path(repo_dir) / ".gitkeep").write_text("")

    _run(repo_dir, "add", "-A")
    # Only commit if there's something staged — repo might genuinely be empty.
    status = _run(repo_dir, "status", "--porcelain")
    if status.stdout.strip():
        _run(repo_dir, "commit", "--quiet", "-m", "init: baseline workspace state")
    else:
        # Truly empty: forge an initial commit so the tag has somewhere to live.
        (Path(repo_dir) / ".gitkeep").write_text("")
        _run(repo_dir, "add", ".gitkeep")
        _run(repo_dir, "commit", "--quiet", "-m", "init: baseline workspace state")

    _run(repo_dir, "tag", "-f", _init_tag(iteration))


def commit_stage(
    repo_dir: str, *, iteration: str, stage: int, stage_name: str,
) -> Optional[str]:
    """Commit the current workspace as ``<iteration>/stage-<N>``.

    Called after a stage's critic PASS, when the workspace is guaranteed
    to be in a quiescent state (no agents writing files). If there's
    nothing new to commit, the tag is still re-pointed at the current
    HEAD so revert ops can address this stage.

    Returns the commit SHA the tag now points to, or ``None`` if the
    repo is not initialized (defensive — caller should ensure_initialized
    first).
    """
    if not _is_initialized(repo_dir):
        logger.warning(
            "[project_repo] commit_stage called on uninitialized repo at {} (stage {}); skipping",
            repo_dir, stage,
        )
        return None

    _run(repo_dir, "add", "-A")
    if _has_uncommitted_changes(repo_dir):
        message = f"Stage {stage}: {stage_name}"
        _run(repo_dir, "commit", "--quiet", "-m", message)

    # Tag (force, so re-runs after a revert overwrite the prior tag).
    tag = _stage_tag(iteration, stage)
    _run(repo_dir, "tag", "-f", tag)

    head_sha = _run(repo_dir, "rev-parse", "HEAD").stdout.strip()
    logger.info("[project_repo] Tagged {} → {}", tag, head_sha[:8])
    return head_sha


def _gen_branch_name(stage: int) -> str:
    return f"feat-stage{stage}-{secrets.token_hex(3)}"


def checkout_branch_from_stage(
    repo_dir: str,
    *,
    iteration: str,
    stage: int,
    branch_name: Optional[str] = None,
) -> str:
    """Create a branch rooted just *before* ``stage`` and check it out.

    Reverting to stage N means re-doing stage N from scratch, so the new
    branch is anchored at:

      - ``<iteration>/stage-<N-1>`` for N >= 2
      - ``<iteration>/init`` for N == 1

    Returns the (possibly auto-generated) branch name.

    Raises ``DirtyWorkspaceError`` if the workspace has uncommitted
    changes (defensive — the engine should only revert at idle moments;
    a dirty worktree means something else is mid-write).

    Raises ``StageNotCommittedError`` if the previous stage hasn't been
    committed yet (e.g. revert to stage 5 when only stages 1-3 are done).
    """
    if _has_uncommitted_changes(repo_dir):
        raise DirtyWorkspaceError(
            f"Workspace {repo_dir} has uncommitted changes; cannot revert safely",
        )

    base_tag = _init_tag(iteration) if stage <= 1 else _stage_tag(iteration, stage - 1)
    if not _tag_exists(repo_dir, base_tag):
        raise StageNotCommittedError(
            f"Cannot revert to stage {stage}: tag {base_tag} not found",
        )

    name = branch_name or _gen_branch_name(stage)
    _run(repo_dir, "checkout", "--quiet", "-b", name, base_tag)
    logger.info(
        "[project_repo] Created branch {} from {} (revert to stage {})",
        name, base_tag, stage,
    )
    return name


def current_branch(repo_dir: str) -> Optional[str]:
    """Return the currently checked-out branch name, or ``None`` for a
    detached HEAD / uninitialized repo."""
    if not _is_initialized(repo_dir):
        return None
    result = _run(repo_dir, "branch", "--show-current", check=False)
    name = result.stdout.strip()
    return name or None


def list_branches(repo_dir: str) -> list[dict]:
    """List all local branches with their head commit SHA and a
    ``current`` flag marking the checked-out one. Returns ``[]`` for
    uninitialized repos."""
    if not _is_initialized(repo_dir):
        return []
    fmt = "%(refname:short)\t%(objectname)"
    result = _run(repo_dir, "for-each-ref", "--format", fmt, "refs/heads/")
    active = current_branch(repo_dir)
    branches: list[dict] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        name, sha = line.split("\t", 1)
        branches.append({
            "name": name,
            "head_commit": sha,
            "current": (name == active),
        })
    return branches
