"""Centralised credential / env-var coordination.

The agent calls :func:`request_env` when it needs one or more env vars
that aren't set yet. The manager:

  1. Writes a placeholder line to ``.env`` so the variable is visible
     in the ENV Management panel even before the user types anything.
  2. Publishes an :data:`EventType.ENV_REQUEST` event the frontend
     turns into a highlighted row.
  3. Awaits a per-key :class:`asyncio.Future` indefinitely — the user
     decides when to fill it in, no timeout.

When the user clicks Save in the ENV panel (or edits ``.env`` directly,
picked up by :func:`_on_env_file_changed`), :func:`save_env` writes the
value, updates :mod:`os.environ`, and resolves any matching futures.
Multiple agents requesting the same key share the same waiter list, so
one Save unblocks all of them.

This module is the single source of truth for credential delivery —
the older chat-based ``request_api_key`` / ``credential_request``
interaction path is removed in favour of it (issue #82 follow-up:
the chat path was easy to miss when the conversation panel was buried,
which is why the experiment runner stalled on infra creds in production).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


# A line such as ``FOO_API_KEY=__OMC_PENDING__`` in ``.env`` is the
# "I need this — user hasn't filled it in yet" marker. The watcher and
# startup-restore both look for it.
PLACEHOLDER_VALUE = "__OMC_PENDING__"


@dataclass
class EnvVarRequest:
    """One pending env-var ask. Multiple agents can attach a Future for
    the same key — they all resolve when the value lands."""
    key: str
    label: str
    secret: bool
    requested_by: str
    reason: str
    future: asyncio.Future = field(repr=False)


# key -> list of pending futures (concurrent agents share the list).
_pending: dict[str, list[EnvVarRequest]] = {}

# Keys we've ever heard about (from .env on disk OR a request_env call).
# Used by ``list_env`` so the frontend can render the full row set.
_known_keys: set[str] = set()

_lock = asyncio.Lock()


def _env_path() -> Path:
    """Return the canonical .env path. Pulled into a function so tests
    can monkeypatch it without touching production config imports."""
    from onemancompany.core.config import DATA_ROOT, DOT_ENV_FILENAME
    return DATA_ROOT / DOT_ENV_FILENAME


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env_file(updates: dict[str, str]) -> None:
    """Add or update each ``key=value`` in ``.env``. Preserves
    surrounding comments and untouched lines."""
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(existing_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in remaining:
            existing_lines[i] = f"{k}={remaining.pop(k)}"
    for k, v in remaining.items():
        existing_lines.append(f"{k}={v}")
    path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")


async def request_env(
    keys: list[dict],
    requested_by: str,
    reason: str,
) -> dict[str, str]:
    """Ask the user for one or more env vars. Blocks until every key
    has a non-placeholder value, then returns ``{key: value, ...}``.

    Each entry in ``keys`` is a dict with:
      - ``name`` (required): env var name (e.g. ``INFRA_SERVER_URL``).
      - ``label`` (optional): human label for the UI; defaults to ``name``.
      - ``secret`` (optional, bool): mask in the UI; defaults to True.

    If the value is already in ``os.environ`` (real, not placeholder),
    we return immediately for that key without prompting.
    """
    loop = asyncio.get_running_loop()
    result: dict[str, str] = {}
    waiters: list[asyncio.Future] = []
    new_requests: list[EnvVarRequest] = []

    for entry in keys:
        name = entry["name"]
        _known_keys.add(name)
        existing = os.environ.get(name)
        if existing and existing != PLACEHOLDER_VALUE:
            result[name] = existing
            continue
        fut: asyncio.Future = loop.create_future()
        req = EnvVarRequest(
            key=name,
            label=entry.get("label", name),
            secret=bool(entry.get("secret", True)),
            requested_by=requested_by,
            reason=reason,
            future=fut,
        )
        _pending.setdefault(name, []).append(req)
        waiters.append(fut)
        new_requests.append(req)

    if not waiters:
        return result

    # Write placeholders so the row is visible in the panel even before
    # the user types anything. Skip ones the user already half-filled.
    on_disk = _read_env_file()
    to_write = {
        r.key: PLACEHOLDER_VALUE
        for r in new_requests
        if on_disk.get(r.key, PLACEHOLDER_VALUE) == PLACEHOLDER_VALUE
    }
    if to_write:
        _write_env_file(to_write)

    await _publish_request_event(new_requests, requested_by, reason)

    # Block forever — user decides when to save. No timeout by design.
    values = await asyncio.gather(*waiters)
    for req, v in zip(new_requests, values):
        result[req.key] = v
    return result


async def _publish_request_event(
    new_requests: list[EnvVarRequest],
    requested_by: str,
    reason: str,
) -> None:
    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.models import EventType
    payload = {
        "keys": [
            {"name": r.key, "label": r.label, "secret": r.secret}
            for r in new_requests
        ],
        "requested_by": requested_by,
        "reason": reason,
    }
    await event_bus.publish(CompanyEvent(
        type=EventType.ENV_REQUEST,
        payload=payload,
    ))


def save_env(updates: dict[str, str]) -> None:
    """Persist ``updates`` to ``.env`` + :mod:`os.environ`, then resolve
    any matching pending futures. Called by the HTTP route the ENV
    panel posts to, and by the .env watcher."""
    if not updates:
        return
    _write_env_file(updates)
    for k, v in updates.items():
        os.environ[k] = v
        _known_keys.add(k)
        waiters = _pending.pop(k, [])
        for req in waiters:
            if not req.future.done():
                req.future.set_result(v)


def _on_env_file_changed() -> None:
    """Filesystem-watcher callback. Re-reads ``.env`` and resolves any
    pending futures whose key now has a real (non-placeholder) value."""
    on_disk = _read_env_file()
    resolved: dict[str, str] = {}
    for k, waiters in list(_pending.items()):
        v = on_disk.get(k)
        if v and v != PLACEHOLDER_VALUE:
            resolved[k] = v
    if resolved:
        save_env(resolved)


def scan_placeholders() -> list[str]:
    """Return keys whose value in ``.env`` is the placeholder. Used by
    the lifespan to re-emit ENV_REQUEST after a restart so the agent
    that was blocked before reboot resumes once the engine is up."""
    return [k for k, v in _read_env_file().items() if v == PLACEHOLDER_VALUE]


def list_env() -> list[dict]:
    """Snapshot for the ENV Management panel.

    Returns one row per known key with name / value / pending flag.
    Secrets are NOT masked here — masking is a frontend concern; the
    backend stays the source of truth."""
    on_disk = _read_env_file()
    names = set(on_disk) | set(_known_keys)
    rows: list[dict] = []
    for name in sorted(names):
        value = on_disk.get(name, os.environ.get(name, ""))
        pending = value == PLACEHOLDER_VALUE or name in _pending
        rows.append({
            "name": name,
            "value": "" if pending else value,
            "pending": pending,
        })
    return rows


# ---------------------------------------------------------------------------
# Filesystem watcher — wired from lifespan in main.py
# ---------------------------------------------------------------------------

_watcher_started = False


def start_env_watcher() -> None:
    """Install a ``watchdog`` observer on the .env directory. Idempotent."""
    global _watcher_started
    if _watcher_started:
        return
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.warning("[env_manager] watchdog not installed; .env hot-reload disabled")
        return

    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    class _Handler(FileSystemEventHandler):
        def on_modified(self, event):
            if Path(event.src_path).name == path.name:
                try:
                    _on_env_file_changed()
                except Exception as exc:
                    logger.warning("[env_manager] watcher callback raised: {}", exc)

    observer = Observer()
    observer.schedule(_Handler(), str(path.parent), recursive=False)
    observer.daemon = True
    observer.start()
    _watcher_started = True
    logger.info("[env_manager] .env watcher started at {}", path)


async def restore_pending_on_startup() -> None:
    """Re-emit ENV_REQUEST for placeholder rows so the user picks up
    where they left off after a restart."""
    pending = scan_placeholders()
    if not pending:
        return
    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.models import EventType
    await event_bus.publish(CompanyEvent(
        type=EventType.ENV_REQUEST,
        payload={
            "keys": [{"name": k, "label": k, "secret": True} for k in pending],
            "requested_by": "system",
            "reason": "Restored from previous session — please fill in.",
        },
    ))
