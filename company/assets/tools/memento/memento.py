"""Memento memory asset tool.

Two LangChain @tool functions: store + recall. Each employee has a private
memory store under EMPLOYEES_DIR/{employee_id}/memory/. Isolation is
enforced server-side via _current_vessel ContextVar — employee_id is never
a tool parameter, so the LLM cannot address another employee's store.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.memory import MemoryV4Adapter
from onemancompany.core.vessel import _current_vessel


_VALID_ROLES = {"user", "assistant"}


def _validate_turns(turns) -> str | None:
    """Return error message string, or None if turns is valid."""
    if not isinstance(turns, list):
        return "turns must be a non-empty list of {role, content} dicts"
    if not turns:
        return "turns must be a non-empty list of {role, content} dicts"
    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            return f"turn {i}: must be a dict with 'role' and 'content'"
        role = turn.get("role")
        content = turn.get("content")
        if not role:
            return f"turn {i}: missing 'role'"
        if role not in _VALID_ROLES:
            return f"turn {i}: invalid role '{role}' (must be 'user' or 'assistant')"
        if not isinstance(content, str) or not content.strip():
            return f"turn {i}: missing or empty 'content'"
    return None


def _resolve_employee_id() -> str:
    vessel = _current_vessel.get(None)
    if vessel is None:
        raise RuntimeError("memento tools require an employee context")
    employee_id = getattr(vessel, "employee_id", "")
    if not employee_id:
        raise RuntimeError("memento tools require an employee context")
    return employee_id


def _employee_memory_dirs(employee_id: str) -> tuple[Path, Path]:
    mem_root = EMPLOYEES_DIR / employee_id / "memory"
    sessions_dir = mem_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return mem_root, sessions_dir


def _next_session_num(sessions_dir: Path) -> int:
    """Highest existing session_num + 1, ignoring non-NNN.json files."""
    highest = 0
    for path in sessions_dir.glob("*.json"):
        stem = path.stem
        if stem.isdigit():
            highest = max(highest, int(stem))
    return highest + 1


def _load_existing_sessions(sessions_dir: Path):
    """Return sorted list of memento Session objects."""
    from onemancompany.core.memory import Session, Turn

    sessions = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[memento] skipping unreadable session {}: {}", path.name, exc)
            continue
        try:
            num = int(data["session_num"])
        except (KeyError, TypeError, ValueError):
            continue
        turns = [
            Turn(speaker=t.get("role", "user"), text=t.get("content", ""))
            for t in data.get("turns", [])
            if isinstance(t, dict) and t.get("content")
        ]
        sessions.append(Session(session_num=num, turns=turns, date_time=data.get("date_time", "")))
    return sessions


def _write_session_file(sessions_dir: Path, session_num: int, turns: list[dict]) -> Path:
    """Atomically write the session JSON. Returns the final path."""
    target = sessions_dir / f"{session_num:03d}.json"
    payload = {
        "session_num": session_num,
        "date_time": datetime.now(timezone.utc).isoformat(),
        "turns": [{"role": t["role"], "content": t["content"]} for t in turns],
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def _build_conversation(sessions_dir: Path, employee_id: str):
    """Load all session files and wrap them in a Conversation."""
    from onemancompany.core.memory import Conversation
    sessions = _load_existing_sessions(sessions_dir)
    return Conversation(
        conv_id=abs(hash(employee_id)) % (10**8),
        sessions=sessions,
    )


async def _run_ingest(adapter, conv, conv_id):
    await adapter.setup()
    await adapter.ingest(conv, conv_id=conv_id)


async def _run_recall(adapter, conv, conv_id, query):
    await adapter.setup()
    await adapter.ingest(conv, conv_id=conv_id)
    return await adapter.recall(query, conv_id=conv_id)


def _build_store_result(mem_root: Path, employee_id: str, session_num: int) -> dict:
    """Read back the new SessionNode + edge counts to populate the result."""
    from onemancompany.core.memory.memento_v4.causal.storage import (
        find_session_node, load_all_edges,
    )

    session_id = f"conv{employee_id}_sess{session_num}"
    memory_dir = mem_root / f"conv_{employee_id}"
    node = find_session_node(memory_dir, session_id) if memory_dir.exists() else None
    edges = load_all_edges(memory_dir) if memory_dir.exists() else []
    edges_for_this = [e for e in edges if e.source_session == session_id]

    sidecar_path = memory_dir / "_v4_meta.json"
    supersede_added = 0
    if sidecar_path.exists():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            sup = sidecar.get("superseded", {})
            for entries in sup.values():
                supersede_added += sum(
                    1 for e in entries
                    if isinstance(e, dict) and e.get("superseded_by") == session_id
                )
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "status": "ok",
        "session_id": session_id,
        "session_num": session_num,
        "title": node.title if node else "",
        "outcome": node.outcome if node else "partial",
        "edges_added": len(edges_for_this),
        "supersede_added": supersede_added,
    }


@tool
def store(turns: list[dict]) -> dict:
    """Persist a finished session into your private long-term memory.

    Call this when a chunk of work concludes — task done, decision made,
    important correction received. The session gets summarized via 1 LLM
    call into a SessionNode (title, goal, outcome, decisions, key quotes,
    files touched), wired into the causal graph (continues / contradicts
    edges to prior sessions), and conflicting facts get supersede flags.

    Args:
        turns: ordered conversation turns. Each turn:
            {"role": "user" | "assistant", "content": "..."}
            Pass enough recent dialogue to capture the substance — the
            finalizer extracts verbatim quotes, so include exact wording
            for decisions, numbers, names, and file paths.
    """
    try:
        employee_id = _resolve_employee_id()
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    err = _validate_turns(turns)
    if err is not None:
        return {"status": "error", "message": err}

    mem_root, sessions_dir = _employee_memory_dirs(employee_id)
    session_num = _next_session_num(sessions_dir)

    try:
        _write_session_file(sessions_dir, session_num, turns)
    except OSError as exc:
        return {"status": "error", "message": f"session write failed: {exc}"}

    from onemancompany.core.memory import AblationFlags

    adapter = MemoryV4Adapter(
        memory_root=mem_root,
        ablation=AblationFlags(reflect_synthesis=False),
    )
    conv = _build_conversation(sessions_dir, employee_id)

    try:
        asyncio.run(_run_ingest(adapter, conv, employee_id))
    except Exception as exc:
        logger.exception("[memento] store ingest failed for {}: {}", employee_id, exc)
        return {
            "status": "error",
            "message": f"finalize failed: {exc}",
            "session_num": session_num,
            "note": "transcript persisted; will retry on next store/recall",
        }

    return _build_store_result(mem_root, employee_id, session_num)


@tool
def recall(query: str, top_k: int = 5) -> dict:
    """Search your private long-term memory for sessions relevant to a query.

    Hybrid retrieval: vector similarity + BM25 lexical match + causal-chain
    BFS expansion (forward up to 5 hops, backward up to 2). Returns a
    markdown context block with the top-K session summaries, linked
    decisions, and supersede notes.

    Args:
        query: natural-language question or topic.
        top_k: how many top sessions to surface (1 to 20, default 5).
    """
    try:
        employee_id = _resolve_employee_id()
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}

    if not isinstance(query, str) or not query.strip():
        return {"status": "error", "message": "query required (non-empty string)"}

    try:
        top_k_int = int(top_k)
    except (TypeError, ValueError):
        top_k_int = 5
    top_k_int = max(1, min(20, top_k_int))

    mem_root, sessions_dir = _employee_memory_dirs(employee_id)

    if not any(sessions_dir.glob("*.json")):
        return {
            "status": "ok",
            "query": query,
            "context": "(no prior sessions)",
            "session_ids": [],
        }

    from onemancompany.core.memory import AblationFlags

    # Use a broad retrieval window (>= 10) so the adapter's BFS seed pool
    # is large enough; trim returned ids to the caller's requested top_k.
    retrieve_k = max(top_k_int, 10)
    adapter = MemoryV4Adapter(
        memory_root=mem_root,
        top_k=retrieve_k,
        ablation=AblationFlags(reflect_synthesis=False),
    )
    conv = _build_conversation(sessions_dir, employee_id)

    try:
        ctx = asyncio.run(_run_recall(adapter, conv, employee_id, query))
    except Exception as exc:
        logger.exception("[memento] recall failed for {}: {}", employee_id, exc)
        return {"status": "error", "message": f"recall failed: {exc}"}

    sids = list(ctx.session_ids or [])[:top_k_int]
    return {
        "status": "ok",
        "query": query,
        "context": ctx.raw_text or "(no relevant sessions)",
        "session_ids": sids,
    }
