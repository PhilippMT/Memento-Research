"""Track Stage 6 run_ids per OMC project by polling infra /api/list_runs.

The remote experiment infra exposes a session-scoped ``/api/list_runs``
endpoint. Each run record includes a ``run_command`` string that begins
``cd omc/<project_id>/<iteration_id>/...`` — the convention enforced by
``experiment-execution-runbook``. This module:

1. Periodically (default 30 s) pulls the run list,
2. Splits runs by which OMC project owns them (via the ``omc/<pid>/<iter>``
   substring in ``run_command``),
3. Persists the per-project run map onto ``pipeline_state.yaml`` under
   ``stage_6_runs`` so a ``GET /api/project/<pid>/runs`` endpoint can
   serve it without re-hitting infra.

No runner-side changes are needed. The poller is the single
source-of-truth on the OMC side; whenever the runbook submits via
``fast_submit.sh``, the next poll picks it up.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger

# Pipeline phases during which a project is actively producing Stage 6
# results and the run-id map should refresh. ``producer_b_waiting`` and
# ``producer_b_finalize`` MUST be included: the whole point of the
# long-running waiter (#93) is for this cron to refresh those projects
# so the engine can detect "all pending runs terminal" and advance.
# ``critic`` and ``gate`` are included so the UI can still show final-
# state runs after producer_b completes. ``done`` runs out of the poll
# cycle after the 6-hour stale window (see ``_should_poll`` below) to
# keep load bounded.
_ACTIVE_PHASES = (
    "producer",
    "producer_b",
    "producer_b_waiting",
    "producer_b_finalize",
    "critic",
    "gate",
    "done",
)

# Stale window in seconds after which a ``phase=done`` project stops being
# polled. 6 hours is generous enough for the UI to render terminal status
# without burning infra requests forever.
_STALE_DONE_SECONDS = 6 * 60 * 60

# Max wall-clock the engine will keep a project parked in
# ``producer_b_waiting`` before surfacing it for CEO intervention. An
# infra-hung run (queued forever, lost on the cluster, network partition)
# would otherwise sit silently with no escape hatch. 12 h covers nearly
# every legitimate experiment (full GSM8K MCTS run on H100 ≈ 6-8 h);
# anything past that is more likely lost than progressing.
_STAGE6_WAITING_MAX_SECONDS = 12 * 60 * 60


def _list_infra_runs(limit: int = 100) -> list[dict[str, Any]]:
    """Call infra ``/api/list_runs`` and return the raw runs list.

    Returns ``[]`` on any failure (missing creds, network, schema drift).
    The cron caller never raises into the system_cron loop; we want this
    function to degrade gracefully rather than poison the engine state.

    Auth convention is via ``session_key`` in the JSON body, not a
    ``Authorization: Bearer`` header — this mirrors what
    ``fast_query_exp_status.sh`` sends to ``/api/list_runs`` and what
    the infra accepts in practice. (Using ``Authorization`` returns
    HTTP 200 with an empty ``runs`` array — the API silently treats
    the session as anonymous.)
    """
    url = os.environ.get("INFRA_SERVER_URL", "")
    key = os.environ.get("INFRA_SESSION_KEY", "")
    if not url or not key:
        return []
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/api/list_runs",
            headers={"Content-Type": "application/json"},
            json={"session_key": key, "limit": limit},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        runs = data.get("runs", [])
        return runs if isinstance(runs, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("[run_tracker] /api/list_runs failed: {}", exc)
        return []


def _filter_for_project(
    runs: list[dict[str, Any]], pid: str, iter_id: str
) -> list[dict[str, Any]]:
    """Return only runs whose ``run_command`` contains ``omc/<pid>/<iter>``.

    The runbook prefixes every submitted command with
    ``cd omc/<project_id>/<iteration_id>/...``; substring match is
    sufficient and avoids fragile parsing of the rest of the command.
    """
    needle = f"omc/{pid}/{iter_id}"
    return [
        r for r in runs
        if needle in (r.get("run_command") or "")
    ]


def _summarise_run(run: dict[str, Any]) -> dict[str, Any]:
    """Reduce a full infra run record to the subset we persist on pipeline state."""
    return {
        "status": run.get("status", ""),
        "run_command": run.get("run_command", ""),
        "actual_cost": run.get("actual_cost", 0),
        "estimated_cost": run.get("estimated_cost", 0),
        "created_at": run.get("created_at", ""),
        "started_at": run.get("started_at", ""),
        "finished_at": run.get("finished_at", ""),
        "error_message": run.get("error_message", ""),
        "metrics": run.get("metrics", {}),
    }


def _should_poll_state(state: dict[str, Any]) -> bool:
    """Decide whether a given ``pipeline_state.yaml`` dict is worth
    polling on this tick. Operates on the raw state dict so we don't
    need a live engine in memory — a server restart should not stop
    tracking for projects that were active before the restart.
    """
    if state.get("current_stage") != 6:
        return False
    phase = state.get("phase")
    if phase not in _ACTIVE_PHASES:
        return False
    if phase == "done":
        # Skip ``done`` projects whose stage-6 commit is older than the
        # stale window — finished projects don't need fresh polls.
        from datetime import datetime, timezone
        started = (state.get("stage_started_at") or {}).get("6", "")
        if not started:
            return True
        try:
            ts = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < _STALE_DONE_SECONDS
    return True


def _iter_active_project_iter_dirs() -> list[tuple[str, str, "Path"]]:
    """Walk ``.onemancompany/.../projects/<pid>/iterations/iter_*`` directories
    and return ``[(pid, iter_id, iter_dir_path)]`` for those whose
    ``pipeline_state.yaml`` says they are an active Stage 6 project.

    Reading from disk lets the tracker survive server restarts: a project
    started before restart no longer has an entry in
    ``pipeline_engine._active_pipelines``, but its state file persists.
    """
    import yaml
    from pathlib import Path
    from onemancompany.core.config import PROJECTS_DIR

    out: list[tuple[str, str, Path]] = []
    if not PROJECTS_DIR.exists():
        return out
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        # Skip ad-hoc / system-reserved project dirs (e.g. ``_adhoc_ceo``).
        if project_dir.name.startswith("_"):
            continue
        pid = project_dir.name
        iters = project_dir / "iterations"
        if not iters.exists():
            continue
        for iter_dir in iters.iterdir():
            state_file = iter_dir / "pipeline_state.yaml"
            if not state_file.is_file():
                continue
            try:
                state = yaml.safe_load(state_file.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            if not isinstance(state, dict):
                continue
            if _should_poll_state(state):
                out.append((pid, iter_dir.name, iter_dir))
    return out


async def poll_active_projects() -> dict[str, int]:
    """Refresh ``stage_6_runs`` on every active OMC project iteration.

    Walks the project dir tree (not in-memory engines), so the poller
    works for any project whose ``pipeline_state.yaml`` says it is
    actively executing Stage 6 — including projects whose engine
    object was GC'd after a server restart.

    Returns a ``{pid: n_runs}`` telemetry map. Cron callers swallow
    the return value; tests assert on it.
    """
    import yaml

    targets = _iter_active_project_iter_dirs()
    if not targets:
        return {}

    all_runs = _list_infra_runs()
    if not all_runs:
        return {pid: 0 for pid, _it, _path in targets}

    # Best-effort sync to in-memory engines so a subsequent
    # ``/api/project/<pid>/runs`` call served by the live engine reads
    # the same data the disk holds.
    from onemancompany.core.pipeline_engine import _active_pipelines

    counts: dict[str, int] = {}
    for pid, iter_id, iter_dir in targets:
        project_runs = _filter_for_project(all_runs, pid, iter_id)
        updated = {
            r.get("run_id"): _summarise_run(r)
            for r in project_runs
            if r.get("run_id")
        }
        counts[pid] = len(updated)

        state_file = iter_dir / "pipeline_state.yaml"
        try:
            state = yaml.safe_load(state_file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(state, dict):
            continue
        if state.get("stage_6_runs") == updated:
            continue  # no change, skip the disk write

        state["stage_6_runs"] = updated
        try:
            state_file.write_text(yaml.safe_dump(state, default_flow_style=False, allow_unicode=True))
        except OSError as exc:
            logger.debug("[run_tracker] failed to save {}: {}", state_file, exc)
            continue
        # Mirror into the live engine if it happens to be loaded.
        eng = _active_pipelines.get(pid)
        if eng is not None:
            eng.state["stage_6_runs"] = updated
        logger.debug(
            "[run_tracker] {} run_ids updated for project {} (iter {})",
            len(updated), pid, iter_id,
        )

        # Stage 6b long-running waiter (#93, #97): if this project is parked
        # in producer_b_waiting, drive its transition from disk state +
        # the just-refreshed run map. Three paths:
        #
        #   (a) every pending run terminal → call on_runs_all_terminal so
        #       the engine advances to producer_b_finalize.
        #   (b) hit the max-wait deadline without all-terminal → escalate
        #       so the project doesn't park forever on an infra-hung run.
        #   (c) still waiting; do nothing, the next tick re-checks.
        #
        # Engine recovery on server restart: ``get_or_load_pipeline`` is
        # called below (lazy-load) so a project whose engine was GC'd
        # before restart still gets driven by this cron. Disk state is
        # already authoritative; the engine handle here is just for the
        # in-memory callback.
        if state.get("phase") == "producer_b_waiting":
            pending = state.get("pending_run_ids") or []
            from onemancompany.core.pipeline_engine import (
                PipelineEngine, get_or_load_pipeline,
            )
            # Lazy-load the engine if it's not already live (restart recovery).
            if eng is None:
                eng = get_or_load_pipeline(pid, str(iter_dir))
            if eng is None:
                logger.warning(
                    "[run_tracker] project {} parked in producer_b_waiting but "
                    "engine cannot be loaded — skipping transition this tick",
                    pid,
                )
            elif pending and PipelineEngine._all_pending_terminal(pending, updated):
                try:
                    eng.on_runs_all_terminal()
                except Exception as exc:  # noqa: BLE001 — must not poison cron
                    logger.warning(
                        "[run_tracker] on_runs_all_terminal raised for project {}: {} — "
                        "engine will retry on next disk-walk recovery",
                        pid, exc,
                    )
            else:
                # Still waiting → check max-wait deadline so an infra-hung
                # run doesn't park the project forever.
                started_iso = state.get("pending_waiting_started_at", "")
                if started_iso:
                    from datetime import datetime, timezone
                    try:
                        started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                        wait_seconds = (datetime.now(timezone.utc) - started).total_seconds()
                    except ValueError:
                        wait_seconds = 0
                    if wait_seconds >= _STAGE6_WAITING_MAX_SECONDS:
                        try:
                            eng.on_runs_wait_timeout(int(wait_seconds))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "[run_tracker] on_runs_wait_timeout raised for project {}: {}",
                                pid, exc,
                            )

    return counts
