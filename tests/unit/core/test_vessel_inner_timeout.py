"""Regression: when an inner ``TimeoutError`` bubbles up from the LLM stream
or a tool call BEFORE the per-task budget is spent, the executor must
retry it like any other transient error — not raise it as a task-level
timeout with the misleading ``"task exceeded 3600s limit"`` message.

Bug: a Stage 2 Literature Survey kept failing after ~4 minutes with
``Timeout: task exceeded 3600s limit`` even though ``task_timeout`` was
3600s. Root cause: ``langchain_openai.stream_chunk_timeout`` fires
``TimeoutError`` mid-stream, the outer ``except TimeoutError: raise``
treats it as a task-level timeout, skips the retry loop, and the result
string hardcodes the 3600s number — telling the user the task ran a full
hour when it actually died in 4 minutes."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _is_inner_timeout — the classifier the retry loop uses to decide
# "transient (retry) vs task-budget exhausted (fail)".
# ---------------------------------------------------------------------------

class TestIsInnerTimeout:
    def test_fast_timeout_is_inner(self):
        """4 minutes elapsed on a 1-hour budget → inner (LLM/tool), retry."""
        from onemancompany.core.vessel import _is_inner_timeout
        assert _is_inner_timeout(elapsed_seconds=254, task_timeout=3600) is True

    def test_exhausted_budget_is_outer(self):
        """Hit the task budget → outer wait_for fired → fail, don't retry."""
        from onemancompany.core.vessel import _is_inner_timeout
        assert _is_inner_timeout(elapsed_seconds=3600, task_timeout=3600) is False

    def test_threshold_is_95pct_by_default(self):
        """A timeout that lands within 5 % of the budget is treated as
        outer — wait_for's scheduling latency can shave a few seconds off
        the nominal deadline, and we'd rather under-retry than spin twice
        on a genuinely exhausted budget."""
        from onemancompany.core.vessel import _is_inner_timeout
        assert _is_inner_timeout(elapsed_seconds=3420, task_timeout=3600) is False  # 95.0%
        assert _is_inner_timeout(elapsed_seconds=3419, task_timeout=3600) is True   # 94.97%

    def test_zero_budget_treated_as_outer(self):
        """Defensive: avoid division-by-zero / always-inner pathologies if
        a misconfigured node ships with timeout_seconds=0."""
        from onemancompany.core.vessel import _is_inner_timeout
        assert _is_inner_timeout(elapsed_seconds=10, task_timeout=0) is False


# ---------------------------------------------------------------------------
# _timeout_failure_message — accurate wording instead of hardcoded 3600.
# ---------------------------------------------------------------------------

class TestTimeoutFailureMessage:
    def test_outer_timeout_names_the_budget(self):
        from onemancompany.core.vessel import _timeout_failure_message
        msg = _timeout_failure_message(elapsed_seconds=3601, task_timeout=3600, cause=TimeoutError("budget"))
        assert "3600" in msg
        assert "exceeded" in msg.lower()

    def test_inner_timeout_names_the_actual_elapsed_and_cause(self):
        """The user-facing message must distinguish a 4-minute inner
        timeout from a 1-hour task overrun, and surface the original
        exception's text so it's diagnosable from the message alone."""
        from onemancompany.core.vessel import _timeout_failure_message
        cause = TimeoutError("langchain_openai.stream_chunk_timeout fired")
        msg = _timeout_failure_message(elapsed_seconds=254, task_timeout=3600, cause=cause)
        # Must not BLAME the budget — the old behaviour reported the budget
        # as "exceeded" even when the inner call died at ~7 % of it.
        assert "exceeded" not in msg.lower(), (
            "must not claim the task budget was exceeded for an inner timeout"
        )
        assert "254" in msg
        assert "stream_chunk_timeout" in msg
