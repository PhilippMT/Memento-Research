"""Regression: the "<agent> needs your input" dialog kept popping up
on every Stage critic dispatch + every subsequent task status update.

Two root causes in ``frontend/src/event-adapter.js``:

  1. The clarification-marker list included broad phrases like
     ``"your decision"`` which matches the **standard critic prompt**:
     ``"Return your decision in JSON format:\\n"`` (lives in
     ``core/conversation.py`` and ``core/routine.py``). So every critic
     dispatch tripped the heuristic.

  2. ``_handleTaskUpdate`` re-emits ``clarification_needed`` on every
     task status change without tracking what it already emitted. The
     same task firing PROCESSING → COMPLETED triggers the popup twice
     even if the markers genuinely match.

The frontend has no JS test harness in this repo (everything is python
+ pytest), so these tests pin the contract at the file-level: source
text must not contain the offending markers, AND must contain a
dedup-by-task-id mechanism (``_clarificationEmittedFor`` / equivalent
identifier). If a future refactor regresses either, this test fails."""
from __future__ import annotations

from pathlib import Path

import pytest


_ADAPTER_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "event-adapter.js"
)


@pytest.fixture(scope="module")
def adapter_source() -> str:
    assert _ADAPTER_PATH.exists(), f"adapter file missing: {_ADAPTER_PATH}"
    return _ADAPTER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Marker list must NOT contain phrases that match standard prompts.
# ---------------------------------------------------------------------------

class TestMarkersAreSpecific:
    @pytest.mark.parametrize("over_broad_marker", [
        "your decision",   # matches "Return your decision in JSON format" (critic prompt)
        "please confirm",  # appears in lots of normal instruction text
        "please provide",  # very common in instructional prose
        "could you confirm",  # mirror of "please confirm"
    ])
    def test_over_broad_marker_removed(self, adapter_source, over_broad_marker):
        """These phrases appear in standard backend prompts (e.g.
        ``Return your decision in JSON format`` lives in
        ``core/conversation.py:466`` and ``core/routine.py``). Matching
        them turns every critic dispatch into a clarification popup."""
        # Scope to the literal markers array body so a comment that
        # mentions the removed phrase (explaining WHY it was removed)
        # doesn't false-flag.
        arr_start = adapter_source.find("const markers = [")
        assert arr_start > 0, "markers array not found in adapter source"
        arr_end = adapter_source.find("];", arr_start)
        assert arr_end > arr_start, "markers array not closed?"
        array_body = adapter_source[arr_start:arr_end]
        for quote in ("'", '"', "`"):
            literal = f"{quote}{over_broad_marker}{quote}"
            assert literal not in array_body, (
                f"clarification marker {literal!r} still listed in the "
                f"markers array — it matches standard prompts and turns "
                f"every critic dispatch into a popup"
            )

    @pytest.mark.parametrize("specific_marker_must_remain", [
        "need your input",
        "please clarify",
        "need clarification",
        "could you clarify",
        "need your approval",
    ])
    def test_specific_markers_kept(self, adapter_source, specific_marker_must_remain):
        """These narrow markers genuinely indicate an agent asking the
        user for input. Locking them in prevents an over-zealous cleanup
        from accidentally gutting the whole heuristic."""
        # Same scoped search as the negative tests above.
        arr_start = adapter_source.find("const markers = [")
        arr_end = adapter_source.find("];", arr_start)
        array_body = adapter_source[arr_start:arr_end]
        present = any(
            f"{q}{specific_marker_must_remain}{q}" in array_body
            for q in ("'", '"', "`")
        )
        assert present, (
            f"marker {specific_marker_must_remain!r} missing — this is a "
            f"legitimate clarification signal that should still fire the popup"
        )


# ---------------------------------------------------------------------------
# 2. Dedup mechanism exists so the same task can't re-fire the popup.
# ---------------------------------------------------------------------------

class TestDedupMechanismExists:
    def test_handle_task_update_dedupes_by_task_id(self, adapter_source):
        """``_handleTaskUpdate`` is called on EVERY task status change.
        Without dedup, a task whose description trips the heuristic
        fires the popup on PROCESSING, then again on COMPLETED, then
        again on next attempt. Pin the dedup contract: there must be a
        Set / Map keyed on task id consulted before emitting."""
        assert "_clarificationEmittedFor" in adapter_source, (
            "dedup state container `_clarificationEmittedFor` missing — "
            "without it the popup re-fires on every task status update"
        )

    def test_dedup_lookup_guards_emit_in_task_update(self, adapter_source):
        """Lock the call shape: the dedup container must be consulted
        BEFORE the emit, not after. Easiest way to verify without a JS
        runner is to scan the ``_handleTaskUpdate`` block specifically."""
        method_start = adapter_source.find("_handleTaskUpdate(p)")
        assert method_start > 0, "_handleTaskUpdate method missing"
        # Bound the search to the next method to keep the assertion local.
        next_method = adapter_source.find("\n  _", method_start + 1)
        method_body = adapter_source[method_start:next_method if next_method > 0 else len(adapter_source)]
        # The guard should appear before the emit('clarification_needed')
        # within the task-update method.
        guard_idx = method_body.find("_clarificationEmittedFor")
        emit_idx = method_body.find("'clarification_needed'")
        assert guard_idx > 0 and emit_idx > 0, (
            "expected both dedup-set lookup and clarification emit in "
            "_handleTaskUpdate"
        )
        assert guard_idx < emit_idx, (
            "_clarificationEmittedFor lookup must appear BEFORE the "
            "clarification emit — otherwise the dedup runs after the popup"
        )
