"""Regression: ``credential_request`` interactions must time out instead
of blocking the agent's awaited Future forever.

Reported: Stage 4 Methodology Designer called ``request_api_key('openrouter',
...)`` because ``OPENROUTER_API_KEY`` wasn't in ``os.environ``. The
backend enqueued an interaction; the agent ``await``ed a Future. In the
AutoResearch frontend there's no conversation UI mounted, so the user
can't respond → Future never resolves → agent blocks indefinitely →
Stage 4 hangs. Only the outer 1-hour task ``wait_for`` would eventually
kill it.

Root cause: ``_start_auto_reply_timer`` early-returns for
``credential_request`` so no timer is ever armed. Other interaction
types correctly start an EA-auto-reply timer; credential is the odd one
out and its rationale ("credential needs a real human") was correct,
but the conclusion ("therefore no timer") leaves the system without an
escape hatch when the human UI is missing.

Fix: arm a credential-specific timer with a longer timeout
(``CREDENTIAL_REQUEST_TIMEOUT``). On fire, resolve the Future with an
empty string. The agent's ``request_api_key`` already maps empty to
``status='no_key'`` and lets the agent fall back."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
class TestCredentialRequestTimeout:
    async def test_constant_exists_and_is_long_enough_for_a_real_human(self):
        """The default timeout must give a real person a chance to find
        and paste a key — but not so long the bug becomes invisible."""
        from onemancompany.core.conversation import CREDENTIAL_REQUEST_TIMEOUT
        # A real human needs at least a few minutes (find their account,
        # generate a key). 30 seconds isn't enough; 30 minutes is the
        # task budget. Pin the band so a future refactor can't trivially
        # short it.
        assert 60.0 <= CREDENTIAL_REQUEST_TIMEOUT <= 1800.0

    async def test_credential_future_resolves_with_empty_string_on_timeout(self, monkeypatch):
        """End-to-end: enqueue a credential_request, wait past the
        (monkey-patched short) timeout, and verify the Future the agent
        is blocking on resolves automatically with empty string —
        which ``request_api_key`` already interprets as ``status='no_key'``."""
        import onemancompany.core.conversation as _conv

        # Shorten the timeout so the test runs in milliseconds.
        monkeypatch.setattr(_conv, "CREDENTIAL_REQUEST_TIMEOUT", 0.05)

        service = _conv.ConversationService()
        conv = await service.get_or_create_oneonone("00007")

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        interaction = _conv.Interaction(
            node_id="cred-test-1",
            tree_path="",
            project_id="",
            source_employee="00007",
            interaction_type="credential_request",
            message="🔑 I need an API key for openrouter.",
            future=future,
            credential_env_key="OPENROUTER_API_KEY",
        )
        await service.enqueue_interaction(conv.id, interaction)

        # The Future must resolve via the timeout path within a generous
        # window relative to the (shortened) timeout. Without the fix this
        # ``wait_for`` raises TimeoutError because the Future never resolves.
        result = await asyncio.wait_for(future, timeout=1.0)
        assert result == "", (
            f"credential timeout should resolve future with empty string "
            f"so request_api_key returns status='no_key'; got {result!r}"
        )

    async def test_credential_pending_is_drained_after_timeout(self, monkeypatch):
        """After the timeout fires, the interaction must be removed from
        the pending queue too — otherwise a later CEO reply tries to
        resolve an already-resolved Future and surfaces weird state."""
        import onemancompany.core.conversation as _conv

        monkeypatch.setattr(_conv, "CREDENTIAL_REQUEST_TIMEOUT", 0.05)

        service = _conv.ConversationService()
        conv = await service.get_or_create_oneonone("00007")

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        interaction = _conv.Interaction(
            node_id="cred-test-2",
            tree_path="",
            project_id="",
            source_employee="00007",
            interaction_type="credential_request",
            message="🔑 need a key",
            future=future,
            credential_env_key="SOME_OTHER_KEY",
        )
        await service.enqueue_interaction(conv.id, interaction)

        await asyncio.wait_for(future, timeout=1.0)
        # Give the timer's finally-block a tick to drain the queue.
        await asyncio.sleep(0.01)

        assert service.get_pending_count(conv.id) == 0, (
            "timed-out credential request must be removed from pending so "
            "a stray CEO reply does not try to resolve a finished Future"
        )

    async def test_real_ceo_reply_cancels_pending_timeout(self, monkeypatch):
        """The timeout must not race with a real CEO reply: if the user
        does respond, the timer is cancelled and ``resolve_interaction``
        delivers the actual key, not empty string."""
        import onemancompany.core.conversation as _conv

        monkeypatch.setattr(_conv, "CREDENTIAL_REQUEST_TIMEOUT", 1.0)

        service = _conv.ConversationService()
        conv = await service.get_or_create_oneonone("00007")

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        interaction = _conv.Interaction(
            node_id="cred-test-3",
            tree_path="",
            project_id="",
            source_employee="00007",
            interaction_type="credential_request",
            message="🔑 need a key",
            future=future,
            credential_env_key="OPENROUTER_API_KEY",
        )
        await service.enqueue_interaction(conv.id, interaction)

        # Simulate user typing the key well before the timeout.
        await asyncio.sleep(0.05)
        # NB: resolve_interaction writes to os.environ; patch
        # update_env_var to avoid touching the real .env on disk.
        import onemancompany.core.config as _cfg
        monkeypatch.setattr(_cfg, "update_env_var", lambda k, v: None)
        await service.resolve_interaction(conv.id, "sk-or-v1-realkey")

        result = await asyncio.wait_for(future, timeout=2.0)
        assert result == "sk-or-v1-realkey", (
            "real CEO reply must win over the timeout, not get clobbered "
            f"by empty string; got {result!r}"
        )
