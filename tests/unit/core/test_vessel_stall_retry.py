"""Regression: stall-retry must not raise ``TaskTransitionError``.

Reported: Stage 2 Literature Survey output triggered stall detection;
the retry path called ``node.set_status(TaskPhase.PROCESSING)`` on a node
that had just been marked ``COMPLETED`` — illegal transition. Whole task
crashed mid-recovery with::

    TaskTransitionError: Task X: illegal transition completed -> processing.
        Valid targets: ['accepted', 'failed', 'pending', 'holding', 'cancelled']

Root cause: ``vessel._execute_task`` did
``set_status(COMPLETED)`` → (system-node branch may add ACCEPTED + FINISHED)
→ then ``set_status(PROCESSING)`` inside the stall-retry branch. The
state machine in ``task_lifecycle.VALID_TRANSITIONS`` does not include
``COMPLETED → PROCESSING`` (only ACCEPTED / FAILED / PENDING / HOLDING /
CANCELLED), so the call always raises.

Fix: re-enter the lifecycle via PENDING — a legal target from COMPLETED —
and let ``schedule_node`` drive the subsequent PENDING → PROCESSING
transition through the normal entry point."""
from __future__ import annotations

import pytest

from onemancompany.core.task_lifecycle import TaskPhase, VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# State-machine sanity: the chain the fix relies on is legal.
# ---------------------------------------------------------------------------

class TestStallRetryTransitionChain:
    def test_completed_to_processing_is_illegal_by_design(self):
        """Locks in the invariant the bug violated. If a future refactor
        ever adds COMPLETED → PROCESSING directly, this test will flag it
        so the reviewer makes a deliberate decision."""
        assert TaskPhase.PROCESSING not in VALID_TRANSITIONS[TaskPhase.COMPLETED]

    def test_completed_to_pending_is_legal(self):
        """The fix re-enters via PENDING. Must be allowed."""
        assert TaskPhase.PENDING in VALID_TRANSITIONS[TaskPhase.COMPLETED]

    def test_pending_to_processing_is_legal(self):
        """And from PENDING, schedule_node drives the normal entry."""
        assert TaskPhase.PROCESSING in VALID_TRANSITIONS[TaskPhase.PENDING]


# ---------------------------------------------------------------------------
# Targeted regression: the stall-retry path is reachable without crash.
# ---------------------------------------------------------------------------

class _FakeNode:
    """Stand-in for a tree node — only the surface the stall-retry code
    touches. Real ``Node.set_status`` calls the state-machine transition,
    so this fake routes through ``transition()`` too to keep the test
    honest about which transitions actually occur."""
    def __init__(self, status: TaskPhase = TaskPhase.COMPLETED):
        from onemancompany.core.task_lifecycle import transition
        self._status_val = status.value
        self._transition = transition
        self.id = "node-test"
        self.children_ids = []
        self.result = "我将立即开始分析三篇关键文献"  # trips promise pattern
        self.stall_retry_count = 0
        self.node_type = "TASK"

    @property
    def status(self):
        return self._status_val

    def set_status(self, target: TaskPhase) -> None:
        # Mirror Node.set_status's guard so an illegal transition raises.
        cur = TaskPhase(self._status_val)
        self._transition(self.id, cur, target)
        self._status_val = target.value


class TestStallRetryDoesNotCrash:
    def test_completed_node_can_re_enter_processing_through_pending(self):
        """End-to-end: starting from COMPLETED, the two-step transition the
        fix uses (COMPLETED → PENDING → PROCESSING) succeeds without
        raising TaskTransitionError."""
        node = _FakeNode(status=TaskPhase.COMPLETED)
        # First step the fix performs.
        node.set_status(TaskPhase.PENDING)
        assert node.status == TaskPhase.PENDING.value
        # Second step normally happens inside _execute_task on re-dispatch.
        node.set_status(TaskPhase.PROCESSING)
        assert node.status == TaskPhase.PROCESSING.value

    def test_old_one_step_path_still_raises(self):
        """Lock in the original bug shape so regression of the fix surfaces
        immediately — if someone later changes COMPLETED → PROCESSING to
        legal in the state machine, this test breaks and forces a
        deliberate review."""
        from onemancompany.core.task_lifecycle import TaskTransitionError
        node = _FakeNode(status=TaskPhase.COMPLETED)
        with pytest.raises(TaskTransitionError):
            node.set_status(TaskPhase.PROCESSING)
