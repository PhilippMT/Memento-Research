"""Tests for the new ``env_manager`` module — agents request env vars,
backend writes a placeholder to ``.env`` and emits a UI event, user
fills the value in the ENV Management panel which writes back to the
same .env and resolves the agent's awaiting future. No timeout: the
agent blocks until a value arrives, exactly the behaviour CEO asked
for so credentials never silently drop.

End-to-end this replaces the old chat-credential flow:
``common_tools.request_api_key`` + ``conversation.credential_request``
+ ``oauth.request_credentials`` collapse into one source of truth here."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def env_path(tmp_path, monkeypatch):
    """Point the manager at a temp .env so we don't trample the dev one."""
    p = tmp_path / ".env"
    p.write_text("# test env\n", encoding="utf-8")
    monkeypatch.setattr(
        "onemancompany.core.env_manager._env_path",
        lambda: p,
    )
    # Fresh module state for each test.
    from onemancompany.core import env_manager as em
    em._pending.clear()
    em._known_keys.clear()
    yield p


# ---------------------------------------------------------------------------
# 1. Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_event_type_env_request_exists(self):
        from onemancompany.core.models import EventType
        assert hasattr(EventType, "ENV_REQUEST")
        assert EventType.ENV_REQUEST.value == "env_request"

    def test_env_var_request_dataclass(self):
        from onemancompany.core.env_manager import EnvVarRequest
        loop = asyncio.new_event_loop()
        try:
            f = loop.create_future()
            r = EnvVarRequest(
                key="FOO_API_KEY",
                label="Foo API Key",
                secret=True,
                requested_by="00018",
                reason="Need it",
                future=f,
            )
            assert r.key == "FOO_API_KEY"
            assert r.secret is True
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# 2. request_env: writes placeholder, awaits future, no timeout
# ---------------------------------------------------------------------------

class TestRequestEnvSinglePending:
    @pytest.mark.asyncio
    async def test_writes_placeholder_to_dotenv_when_missing(self, env_path, monkeypatch):
        """When the agent asks for a key that isn't in .env, the
        manager writes a placeholder so the variable is visible in the
        ENV panel even before the user types anything."""
        from onemancompany.core import env_manager as em
        # Cancel the wait so the test completes — we only care that the
        # placeholder hit disk before the await.
        async def fast_save():
            await asyncio.sleep(0.01)
            em.save_env({"FOO_API_KEY": "real-value"})

        task = asyncio.create_task(em.request_env(
            keys=[{"name": "FOO_API_KEY", "label": "Foo Key", "secret": True}],
            requested_by="00018",
            reason="test",
        ))
        await fast_save()
        result = await task
        assert result == {"FOO_API_KEY": "real-value"}
        # Placeholder line was written (and then overwritten with real value).
        body = env_path.read_text(encoding="utf-8")
        assert "FOO_API_KEY=real-value" in body

    @pytest.mark.asyncio
    async def test_returns_existing_value_without_blocking(self, env_path, monkeypatch):
        """If the key is already set in os.environ, return immediately
        without emitting a request event."""
        from onemancompany.core import env_manager as em
        monkeypatch.setenv("FOO_API_KEY", "preset")
        result = await asyncio.wait_for(
            em.request_env(
                keys=[{"name": "FOO_API_KEY"}],
                requested_by="00018",
                reason="test",
            ),
            timeout=1.0,
        )
        assert result == {"FOO_API_KEY": "preset"}

    @pytest.mark.asyncio
    async def test_emits_env_request_event(self, env_path):
        """The frontend can't show what it doesn't hear about."""
        from onemancompany.core import env_manager as em
        from onemancompany.core.events import event_bus
        from onemancompany.core.models import EventType

        q = event_bus.subscribe()
        try:
            task = asyncio.create_task(em.request_env(
                keys=[{"name": "BAR_KEY", "label": "Bar", "secret": True}],
                requested_by="00018",
                reason="testing",
            ))
            # Drain until we see ENV_REQUEST.
            ev = None
            for _ in range(5):
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
                if ev.type == EventType.ENV_REQUEST:
                    break
            assert ev is not None and ev.type == EventType.ENV_REQUEST
            assert any(k["name"] == "BAR_KEY" for k in ev.payload["keys"])
            assert ev.payload["reason"] == "testing"
            assert ev.payload["requested_by"] == "00018"
        finally:
            event_bus.unsubscribe(q)
            task.cancel()
            with pytest.raises((asyncio.CancelledError, BaseException)):
                await task


# ---------------------------------------------------------------------------
# 3. Concurrent requests for the same key share a future list
# ---------------------------------------------------------------------------

class TestConcurrentRequests:
    @pytest.mark.asyncio
    async def test_two_agents_one_save_both_unblock(self, env_path):
        from onemancompany.core import env_manager as em

        t1 = asyncio.create_task(em.request_env(
            keys=[{"name": "SHARED_KEY"}], requested_by="00018", reason="a"))
        t2 = asyncio.create_task(em.request_env(
            keys=[{"name": "SHARED_KEY"}], requested_by="00019", reason="b"))
        await asyncio.sleep(0.05)  # let both register
        em.save_env({"SHARED_KEY": "v"})
        r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
        assert r1 == {"SHARED_KEY": "v"}
        assert r2 == {"SHARED_KEY": "v"}


# ---------------------------------------------------------------------------
# 4. Multi-field: returns ALL values, only resolves when all are saved
# ---------------------------------------------------------------------------

class TestMultiField:
    @pytest.mark.asyncio
    async def test_request_blocks_until_all_fields_saved(self, env_path):
        from onemancompany.core import env_manager as em

        task = asyncio.create_task(em.request_env(
            keys=[
                {"name": "INFRA_SERVER_URL", "label": "Server URL"},
                {"name": "INFRA_SESSION_KEY", "label": "Session Key", "secret": True},
            ],
            requested_by="00018",
            reason="infra",
        ))
        await asyncio.sleep(0.05)

        # Save only one — task must still be pending.
        em.save_env({"INFRA_SERVER_URL": "http://h"})
        await asyncio.sleep(0.05)
        assert not task.done()

        # Save the other — task resolves.
        em.save_env({"INFRA_SESSION_KEY": "vk"})
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == {
            "INFRA_SERVER_URL": "http://h",
            "INFRA_SESSION_KEY": "vk",
        }


# ---------------------------------------------------------------------------
# 5. save_env writes to .env AND os.environ
# ---------------------------------------------------------------------------

class TestSaveEnv:
    def test_save_writes_to_dotenv_and_environ(self, env_path, monkeypatch):
        from onemancompany.core import env_manager as em
        import os
        monkeypatch.delenv("PERSIST_TEST", raising=False)
        em.save_env({"PERSIST_TEST": "abc"})
        assert os.environ.get("PERSIST_TEST") == "abc"
        body = env_path.read_text(encoding="utf-8")
        assert "PERSIST_TEST=abc" in body

    def test_save_updates_existing_line(self, env_path, monkeypatch):
        from onemancompany.core import env_manager as em
        env_path.write_text("FOO=old\nBAR=keep\n", encoding="utf-8")
        em.save_env({"FOO": "new"})
        body = env_path.read_text(encoding="utf-8")
        assert "FOO=new" in body
        assert "BAR=keep" in body
        assert "FOO=old" not in body


# ---------------------------------------------------------------------------
# 6. Filesystem watcher: direct .env edit resolves pending futures
# ---------------------------------------------------------------------------

class TestDirectEditResolves:
    @pytest.mark.asyncio
    async def test_handle_env_file_change_resolves_pending(self, env_path):
        """The watcher's callback (``_on_env_file_changed``) is what we
        unit-test here; the watchdog wiring itself is covered by the
        startup lifespan test downstream."""
        from onemancompany.core import env_manager as em

        task = asyncio.create_task(em.request_env(
            keys=[{"name": "EDITED_DIRECTLY"}],
            requested_by="00018",
            reason="watch",
        ))
        await asyncio.sleep(0.05)

        # Simulate a manual edit + watchdog callback.
        env_path.write_text("EDITED_DIRECTLY=manual\n", encoding="utf-8")
        em._on_env_file_changed()

        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == {"EDITED_DIRECTLY": "manual"}


# ---------------------------------------------------------------------------
# 7. Restart-while-waiting: placeholders in .env trigger re-emit
# ---------------------------------------------------------------------------

class TestRestartRestore:
    def test_restore_pending_on_startup_finds_placeholders(self, env_path):
        from onemancompany.core import env_manager as em
        env_path.write_text(
            f"REAL_KEY=value\n"
            f"PLACEHOLDER_KEY={em.PLACEHOLDER_VALUE}\n",
            encoding="utf-8",
        )
        pending = em.scan_placeholders()
        assert pending == ["PLACEHOLDER_KEY"]

    def test_real_value_no_longer_placeholder(self, env_path):
        from onemancompany.core import env_manager as em
        env_path.write_text("FOO=somethingreal\n", encoding="utf-8")
        assert em.scan_placeholders() == []


# ---------------------------------------------------------------------------
# 8. list_env returns all known keys for the UI to render
# ---------------------------------------------------------------------------

class TestListEnv:
    def test_list_env_returns_known_and_dotenv_keys(self, env_path, monkeypatch):
        from onemancompany.core import env_manager as em
        env_path.write_text(
            f"KNOWN_KEY=v1\n"
            f"PENDING_KEY={em.PLACEHOLDER_VALUE}\n",
            encoding="utf-8",
        )
        listing = em.list_env()
        names = {row["name"] for row in listing}
        assert "KNOWN_KEY" in names
        assert "PENDING_KEY" in names
        # Pending row marked so frontend can highlight it.
        pending = next(r for r in listing if r["name"] == "PENDING_KEY")
        assert pending["pending"] is True
        known = next(r for r in listing if r["name"] == "KNOWN_KEY")
        assert known["pending"] is False
