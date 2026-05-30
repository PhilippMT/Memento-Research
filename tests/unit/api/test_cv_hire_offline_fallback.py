"""When the Talent Market is unreachable, `hire-from-cv` must still register
pure-prompt talents (no tools) from CV data, while talents that declare tools
(which genuinely need their repo) keep failing. Regression for the offline
AutoResearch-roster bootstrap."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import onemancompany.api.routes as routes


def _run_captured(body):
    """Call hire_from_cv with spawn_background patched to capture the coroutine,
    then await it. Returns (execute_hire_mock, publish_mock)."""
    captured = {}

    def _capture(coro):
        captured["coro"] = coro
        return AsyncMock()  # stand-in Task

    onboard_mock = AsyncMock(side_effect=RuntimeError("Talent Market unreachable"))
    exec_mock = AsyncMock(return_value=type("E", (), {"id": "00099"})())
    publish_mock = AsyncMock()

    with patch.object(routes, "spawn_background", _capture), \
         patch("onemancompany.agents.onboarding.execute_hire", exec_mock), \
         patch("onemancompany.agents.onboarding.generate_nickname",
               AsyncMock(return_value="测试")), \
         patch("onemancompany.agents.recruitment.talent_market.onboard", onboard_mock), \
         patch.object(routes.event_bus, "publish", publish_mock):
        asyncio.run(routes.hire_from_cv({"cv": body}))
        # _do_cv_hire was deferred to spawn_background → run it now
        asyncio.run(captured["coro"])

    return exec_mock, publish_mock


def _cv(tools):
    return {
        "name": "Topic Refiner", "role": "Researcher",
        "talent_id": "topic-refiner", "hosting": "company",
        "skills": ["topic_refiner"], "tools": tools,
        "system_prompt_template": "You refine topics.",
        "source_type": "talent_market",
    }


def test_pure_prompt_talent_hires_when_market_down():
    """tools=[] → market failure degrades to CV-only hire (execute_hire called)."""
    exec_mock, publish_mock = _run_captured(_cv(tools=[]))
    assert exec_mock.await_count == 1, "pure-prompt talent should still be hired offline"
    # no repo-fetch error surfaced
    errs = [c for c in publish_mock.await_args_list
            if "Failed to fetch repo URL" in str(c)]
    assert not errs, "should not publish a repo-fetch error for a tools=0 talent"


def test_tooled_talent_still_fails_when_market_down():
    """tools=[...] → genuinely needs repo → still aborts (no hire)."""
    exec_mock, publish_mock = _run_captured(_cv(tools=[{"name": "do_thing"}]))
    assert exec_mock.await_count == 0, "tooled talent must NOT be hired without its repo"
