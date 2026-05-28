"""Per-user LLM key dispatch.

Each logged-in user can be mapped to their own LLM key (e.g. a LiteLLM virtual
key with its own budget) so their agents run on the *user's* token instead of
the single shared global key. Fully optional and zero-regression: when no
mapping resolves, callers fall back to the global key — identical to the
previous behaviour.

Flow:
  1. ``set_project_owner(project_id, user_id)`` is called when a logged-in user
     launches a pipeline (from the request cookie's JWT).
  2. While an agent task for that project runs, the vessel resolves the owner's
     key and sets the ``current_user_llm`` contextvar.
  3. ``make_llm()`` reads the contextvar and, if present, uses that key/base_url/
     model instead of the global ``CUSTOM_API_KEY``.

Two small JSON stores live under the company data dir:
  user_llm_keys.json   {"<user_id>": {"api_key": "...", "base_url": "...", "model": "..."}}
  project_owners.json  {"<base_project_id>": "<user_id>"}
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

from onemancompany.core.config import DATA_ROOT

# Resolved LLM override for the current agent task (None → use the global key).
current_user_llm: ContextVar[dict | None] = ContextVar("current_user_llm", default=None)

_KEYS_FILE = DATA_ROOT / "user_llm_keys.json"
_OWNERS_FILE = DATA_ROOT / "project_owners.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("[user_llm] failed reading {}: {}", path, e)
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _base_pid(project_id: str) -> str:
    """Project ids look like ``<pid>/<iter>`` — ownership is keyed by the base pid."""
    return (project_id or "").split("/", 1)[0]


def set_project_owner(project_id: str, user_id: str) -> None:
    """Record which logged-in user launched a project (best-effort, non-fatal)."""
    if not project_id or not user_id:
        return
    data = _read_json(_OWNERS_FILE)
    data[_base_pid(project_id)] = str(user_id)
    _write_json(_OWNERS_FILE, data)


def get_project_owner(project_id: str) -> str:
    return _read_json(_OWNERS_FILE).get(_base_pid(project_id), "")


def resolve_user_llm(user_id: str) -> dict | None:
    """Return ``{api_key, base_url?, model?}`` for this user, or None for global."""
    if not user_id:
        return None
    entry = _read_json(_KEYS_FILE).get(str(user_id))
    if isinstance(entry, dict) and entry.get("api_key"):
        return entry
    return None


def resolve_for_project(project_id: str) -> dict | None:
    """Convenience: project_id → owner → per-user LLM override (or None)."""
    return resolve_user_llm(get_project_owner(project_id))
