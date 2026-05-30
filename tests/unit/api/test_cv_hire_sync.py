"""``hire-from-cv`` accepts ``sync=True`` so the startup script (``start.sh``)
can block until the hire is actually on the roster.

Default behaviour (``sync`` omitted or False) keeps the existing fire-and-forget
spawn_background flow used by the CEO-driven UI hire path."""
import asyncio
from unittest.mock import AsyncMock, patch

import onemancompany.api.routes as routes


def _cv(talent_id: str = "topic-refiner"):
    return {
        "name": "Topic Refiner", "role": "Researcher",
        "talent_id": talent_id, "hosting": "company",
        "skills": ["topic_refiner"], "tools": [],
        "system_prompt_template": "You refine topics.",
        "source_type": "talent_market",
    }


def _patched_run(body, *, exec_side_effect=None):
    """Run ``hire_from_cv(body)`` with execute_hire / nickname / onboard /
    event_bus mocked. Returns (spawn_calls, exec_mock, response).

    The async-mode coroutine is captured but NOT awaited — the routing
    assertion (``spawn_calls == 1``) is what we care about, and running
    the body would re-cover the offline-fallback test's scope.
    """
    spawn_calls = []

    def _capture(coro):
        spawn_calls.append(coro)
        coro.close()  # close to suppress "coroutine was never awaited" warnings
        return AsyncMock()

    onboard_mock = AsyncMock(return_value={"repo_url": ""})
    if exec_side_effect is not None:
        exec_mock = AsyncMock(side_effect=exec_side_effect)
    else:
        exec_mock = AsyncMock(return_value=type("E", (), {"id": "00099"})())
    publish_mock = AsyncMock()

    with patch.object(routes, "spawn_background", _capture), \
         patch("onemancompany.agents.onboarding.execute_hire", exec_mock), \
         patch("onemancompany.agents.onboarding.generate_nickname",
               AsyncMock(return_value="测试")), \
         patch("onemancompany.agents.recruitment.talent_market.onboard", onboard_mock), \
         patch.object(routes.event_bus, "publish", publish_mock):
        response = asyncio.run(routes.hire_from_cv(body))

    return spawn_calls, exec_mock, response


def test_async_mode_defers_hire_via_spawn_background():
    """Default (no sync flag) preserves the existing behaviour used by the
    CEO-driven UI: respond immediately with ``status=onboarding`` and run
    the actual hire in the background."""
    spawn_calls, exec_mock, response = _patched_run({"cv": _cv()})
    assert response["status"] == "onboarding"
    assert exec_mock.await_count == 0, "execute_hire must run in the background, not inline"
    assert len(spawn_calls) == 1, "_do_cv_hire must be deferred to spawn_background"


def test_sync_mode_awaits_hire_inline_and_returns_hired_status():
    """``sync=True`` (used by start.sh) must await ``_do_cv_hire`` inline so
    the HTTP response only returns after the employee is actually on the
    roster — preventing the frontend from loading before hires complete."""
    spawn_calls, exec_mock, response = _patched_run({"cv": _cv(), "sync": True})
    assert exec_mock.await_count == 1, "sync mode must await execute_hire before responding"
    assert spawn_calls == [], "sync mode must not use spawn_background"
    assert response["status"] == "hired"


def test_sync_mode_reports_failed_status_when_hire_raises():
    """Regression: ``_do_cv_hire`` used to swallow every failure path and
    return None. Sync mode then misreported ``status=hired`` even when no
    employee was actually registered, so start.sh printed ✓ on broken
    hires. The endpoint must surface failure as ``status=failed`` so the
    bootstrap loop can distinguish real successes from silent failures."""
    spawn_calls, exec_mock, response = _patched_run(
        {"cv": _cv(), "sync": True},
        exec_side_effect=RuntimeError("execute_hire blew up"),
    )
    assert exec_mock.await_count == 1, "execute_hire must have been attempted"
    assert response["status"] == "failed", (
        "sync mode must report failure when _do_cv_hire's happy-path return "
        "was not reached"
    )
