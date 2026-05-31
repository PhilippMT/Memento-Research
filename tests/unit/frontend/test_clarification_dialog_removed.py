"""The "<agent> needs your input" floating chat dialog is removed
in its entirety.

History: the dialog activated through three paths in
``frontend/src/event-adapter.js`` —

  1. Chat-text heuristic in ``_handleMeeting`` (agent A says "please
     clarify" in a meeting → popup at the user). False signal: meetings
     are agent ↔ agent conversations, the user isn't a participant.
  2. Task-description heuristic in ``_handleTaskUpdate`` (``_isClarification
     Request(desc)`` matched "Return your decision in JSON format" from the
     standard critic prompt). False signal: descriptions are prompts to
     other agents, not requests for user input.
  3. ``pending_interaction`` WebSocket event → ``_handleClarification``.
     Dead code — no backend emitter exists.

The reliable "agent asks user" path was always ``node_type='CEO_REQUEST'``
(or the explicit ``p.ceo_request`` flag), which the breakpoint /
action-panel UI already handles. So the whole dialog system was strictly
duplicate, noisy machinery on top of a working mechanism. This test
pins its removal at the source level."""
from __future__ import annotations

from pathlib import Path

import pytest


_FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
_ADAPTER = _FRONTEND / "src" / "event-adapter.js"
_PIPELINE_CTRL = _FRONTEND / "src" / "pipeline-controller.js"
_INDEX_HTML = _FRONTEND / "index.html"


@pytest.fixture(scope="module")
def adapter_source() -> str:
    return _ADAPTER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pipeline_ctrl_source() -> str:
    return _PIPELINE_CTRL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_source() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# event-adapter.js — handler + heuristics gone.
# ---------------------------------------------------------------------------

class TestAdapterClarificationGone:
    @pytest.mark.parametrize("symbol", [
        "clarification_needed",
        "_handleClarification",
        "_isClarificationRequest",
        "pending_interaction",
        # PR #78's dedup set should also be removed since the whole
        # emission path is gone.
        "_clarificationEmittedFor",
    ])
    def test_symbol_removed_from_adapter(self, adapter_source, symbol):
        assert symbol not in adapter_source, (
            f"adapter still references `{symbol}` — the entire "
            f"clarification-dialog system was supposed to be removed"
        )


# ---------------------------------------------------------------------------
# pipeline-controller.js — registration + handler gone.
# ---------------------------------------------------------------------------

class TestPipelineControllerClarificationGone:
    @pytest.mark.parametrize("symbol", [
        "clarification_needed",
        "handleClarification",
        "openChatDialog",
    ])
    def test_symbol_removed_from_pipeline_controller(self, pipeline_ctrl_source, symbol):
        assert symbol not in pipeline_ctrl_source, (
            f"pipeline-controller still references `{symbol}` — the "
            f"clarification dialog hookup should be gone"
        )


# ---------------------------------------------------------------------------
# index.html — dialog component, CSS, globals gone.
# ---------------------------------------------------------------------------

class TestIndexHtmlClarificationGone:
    @pytest.mark.parametrize("symbol", [
        "openChatDialog",
        "closeChatDialog",
        "isChatDialogOpen",
        "_chatDialog",
        "_chatAgentName",
        "_chatEmployeeId",
        "_chatMessages",
        # NB: the ``.chat-dialog*`` CSS classes intentionally remain — the
        # breakpoint dialog reuses the same visual styling. Only the
        # JS that opens the "needs your input" popup is removed.
        # The literal label the user reported seeing must be gone.
        "needs your input",
    ])
    def test_symbol_removed_from_index(self, index_source, symbol):
        assert symbol not in index_source, (
            f"index.html still references `{symbol}` — the chat-dialog "
            f"popup component should be fully removed"
        )
