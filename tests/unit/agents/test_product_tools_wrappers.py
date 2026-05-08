from __future__ import annotations

import pytest

from onemancompany.agents import product_tools as pt


@pytest.mark.asyncio
async def test_create_product_tool_parses_key_results_and_skips_bad_target(monkeypatch):
    added = []
    monkeypatch.setattr(pt, "_resolve_caller_id", lambda: "00010")
    monkeypatch.setattr(pt.prod, "create_product", lambda name, owner_id, description: {"slug": "app"})
    monkeypatch.setattr(pt.prod, "add_key_result", lambda slug, title, target, unit: added.append((slug, title, target, unit)))

    result = await pt.create_product_tool.ainvoke(
        {
            "name": "App",
            "description": "Desc",
            "key_results": "Users|100|people;Bad|not-number|x;Latency|2",
        }
    )

    assert "Created product 'App'" in result
    assert "with 2 key results" in result
    assert added == [("app", "Users", 100.0, "people"), ("app", "Latency", 2.0, "")]


@pytest.mark.asyncio
async def test_create_product_issue_validation_and_errors(monkeypatch):
    monkeypatch.setattr(pt, "_resolve_caller_id", lambda: "00010")

    assert "invalid priority" in await pt.create_product_issue.ainvoke(
        {"product_slug": "app", "title": "Bug", "description": "Desc", "priority": "bad"}
    )

    def raise_value_error(*args, **kwargs):
        raise ValueError("bad product")

    monkeypatch.setattr(pt.prod, "create_issue", raise_value_error)
    assert "bad product" in await pt.create_product_issue.ainvoke(
        {"product_slug": "app", "title": "Bug", "description": "Desc", "priority": "P1"}
    )


def test_resolve_caller_id_fallbacks(monkeypatch):
    assert pt._resolve_caller_id() == "agent"


@pytest.mark.asyncio
async def test_get_product_context_with_krs_and_open_issues(monkeypatch):
    monkeypatch.setattr(
        pt.prod,
        "load_product",
        lambda slug: {
            "name": "App",
            "status": "active",
            "current_version": "1.2.3",
            "description": "Ship it",
            "key_results": [{"title": "Users", "current": 25, "target": 100}],
        },
    )
    monkeypatch.setattr(
        pt.prod,
        "list_issues",
        lambda slug, **kwargs: [
            {"id": "i1", "title": "Open", "priority": "P1", "status": "backlog"},
            {"id": "i2", "title": "Done", "priority": "P2", "status": "done"},
        ],
    )

    result = await pt.get_product_context_tool.ainvoke({"product_slug": "app"})

    assert "# App" in result
    assert "Users: 25/100 (25%)" in result
    assert "Open Issues (1)" in result
    assert "Open" in result


@pytest.mark.asyncio
async def test_get_product_context_not_found(monkeypatch):
    monkeypatch.setattr(pt.prod, "load_product", lambda slug: None)
    assert await pt.get_product_context_tool.ainvoke({"product_slug": "missing"}) == "Product 'missing' not found"


@pytest.mark.asyncio
async def test_issue_update_close_and_filters(monkeypatch):
    update_calls = []
    monkeypatch.setattr(pt.prod, "update_issue", lambda slug, issue_id, **updates: update_calls.append(updates) or {"id": issue_id})
    monkeypatch.setattr(pt.prod, "close_issue", lambda slug, issue_id, resolution: {"id": issue_id})
    monkeypatch.setattr(
        pt.prod,
        "list_issues",
        lambda slug, **kwargs: [{"id": "i1", "title": "Bug", "priority": "P0", "status": "planned"}],
    )

    result = await pt.update_product_issue.ainvoke(
        {
            "product_slug": "app",
            "issue_id": "i1",
            "status": "planned",
            "priority": "P0",
            "assignee_id": "00010",
            "labels": "bug, backend",
        }
    )
    assert "Updated i1" in result
    assert update_calls == [{"status": "planned", "priority": "P0", "assignee_id": "00010", "labels": ["bug", "backend"]}]

    assert await pt.update_product_issue.ainvoke({"product_slug": "app", "issue_id": "i1"}) == "Error: no fields to update"
    assert "invalid resolution" in await pt.close_product_issue.ainvoke({"product_slug": "app", "issue_id": "i1", "resolution": "bad"})
    assert await pt.close_product_issue.ainvoke({"product_slug": "app", "issue_id": "i1", "resolution": "fixed"}) == "Closed i1 as fixed"

    listed = await pt.list_product_issues_tool.ainvoke({"product_slug": "app", "status": "planned", "priority": "P0"})
    assert "[P0] Bug (i1) [planned]" in listed


@pytest.mark.asyncio
async def test_issue_not_found_and_errors(monkeypatch):
    monkeypatch.setattr(pt.prod, "update_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(pt.prod, "close_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(pt.prod, "list_issues", lambda *args, **kwargs: [])

    assert "not found" in await pt.update_product_issue.ainvoke({"product_slug": "app", "issue_id": "missing", "status": "done"})
    assert "not found" in await pt.close_product_issue.ainvoke({"product_slug": "app", "issue_id": "missing", "resolution": "fixed"})
    assert await pt.list_product_issues_tool.ainvoke({"product_slug": "app"}) == "No issues found"

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("missing product")

    monkeypatch.setattr(pt.prod, "update_issue", raise_missing)
    monkeypatch.setattr(pt.prod, "close_issue", raise_missing)
    assert "missing product" in await pt.update_product_issue.ainvoke({"product_slug": "app", "issue_id": "i1", "status": "done"})
    assert "missing product" in await pt.close_product_issue.ainvoke({"product_slug": "app", "issue_id": "i1", "resolution": "fixed"})


@pytest.mark.asyncio
async def test_sprint_tools(monkeypatch):
    monkeypatch.setattr(pt.prod, "create_sprint", lambda **kwargs: {"id": "s1"})
    monkeypatch.setattr(pt.prod, "get_active_sprint", lambda slug: {"id": "s1", "name": "Sprint", "status": "active", "goal": "", "start_date": "2026-01-01", "end_date": "2026-01-14", "capacity": 10})
    monkeypatch.setattr(pt.prod, "close_sprint", lambda slug, sprint_id: {"velocity": 8, "completion_rate": 80, "carry_over_count": 1, "retrospective": "Good"})
    monkeypatch.setattr(pt.prod, "load_sprint", lambda slug, sprint_id: {"id": sprint_id, "name": "Sprint", "status": "active", "goal": "Goal", "start_date": "2026-01-01", "end_date": "2026-01-14", "capacity": 10})
    monkeypatch.setattr(pt.prod, "list_sprints", lambda slug: [])
    monkeypatch.setattr(pt.prod, "list_issues", lambda slug, sprint=None, **kwargs: [{"status": "done", "story_points": 5}, {"status": "backlog", "story_points": 3}])
    monkeypatch.setattr(pt.prod, "suggest_capacity", lambda slug: 12)

    created = await pt.create_sprint_tool.ainvoke(
        {"product_slug": "app", "name": "Sprint", "start_date": "2026-01-01", "end_date": "2026-01-14", "capacity": "10"}
    )
    assert "Created sprint" in created
    assert "velocity=8" in await pt.close_sprint_tool.ainvoke({"product_slug": "app"})
    info = await pt.get_sprint_info_tool.ainvoke({"product_slug": "app", "sprint_id": "s1"})
    assert "Points: 5/8" in info
    assert "Suggested capacity" in info


@pytest.mark.asyncio
async def test_sprint_info_fallback_and_no_active(monkeypatch):
    monkeypatch.setattr(pt.prod, "get_active_sprint", lambda slug: None)
    monkeypatch.setattr(pt.prod, "list_sprints", lambda slug: [{"id": "s1", "name": "Past", "status": "closed", "start_date": "2026-01-01", "end_date": "2026-01-14"}])

    assert "No active sprint" in await pt.get_sprint_info_tool.ainvoke({"product_slug": "app"})
    assert "No active sprint found" in await pt.close_sprint_tool.ainvoke({"product_slug": "app"})


@pytest.mark.asyncio
async def test_sprint_tool_errors(monkeypatch):
    def raise_value_error(*args, **kwargs):
        raise ValueError("bad sprint")

    monkeypatch.setattr(pt.prod, "create_sprint", raise_value_error)
    monkeypatch.setattr(pt.prod, "close_sprint", raise_value_error)
    monkeypatch.setattr(pt.prod, "load_sprint", lambda slug, sprint_id: None)
    monkeypatch.setattr(pt.prod, "list_sprints", lambda slug: [])

    assert "bad sprint" in await pt.create_sprint_tool.ainvoke(
        {"product_slug": "app", "name": "Sprint", "start_date": "2026-01-01", "end_date": "2026-01-14"}
    )
    assert "bad sprint" in await pt.close_sprint_tool.ainvoke({"product_slug": "app", "sprint_id": "s1"})
    assert "No sprints found" in await pt.get_sprint_info_tool.ainvoke({"product_slug": "app", "sprint_id": "missing"})


@pytest.mark.asyncio
async def test_issue_link_and_blocked_tools(monkeypatch):
    linked = []
    monkeypatch.setattr(pt.prod, "add_issue_link", lambda slug, issue_id, target_id, relation: linked.append((issue_id, target_id, relation.value)))
    monkeypatch.setattr(pt.prod, "remove_issue_link", lambda slug, issue_id, target_id: None)
    monkeypatch.setattr(pt.prod, "is_blocked", lambda slug, issue_id: issue_id == "i1")
    monkeypatch.setattr(
        pt.prod,
        "list_issues",
        lambda slug: [
            {"id": "i1", "title": "Blocked", "priority": "P1", "status": "planned", "issue_links": [{"relation": "blocked_by", "issue_id": "i0"}]},
            {"id": "i2", "title": "Done", "priority": "P2", "status": "done", "issue_links": []},
        ],
    )

    assert "invalid relation" in await pt.link_issues_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "target_id": "i2", "relation": "bad"})
    assert "Linked i1" in await pt.link_issues_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "target_id": "i2", "relation": "blocks"})
    assert linked == [("i1", "i2", "blocks")]
    assert "Unlinked i1" in await pt.unlink_issues_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "target_id": "i2"})
    assert "Blocked issues (1)" in await pt.check_blocked_issues_tool.ainvoke({"product_slug": "app"})


@pytest.mark.asyncio
async def test_issue_link_errors_and_no_blocked(monkeypatch):
    def raise_value_error(*args, **kwargs):
        raise ValueError("bad link")

    monkeypatch.setattr(pt.prod, "add_issue_link", raise_value_error)
    monkeypatch.setattr(pt.prod, "remove_issue_link", raise_value_error)
    monkeypatch.setattr(pt.prod, "list_issues", lambda slug: [{"id": "i1", "title": "Open", "status": "planned", "issue_links": []}])
    monkeypatch.setattr(pt.prod, "is_blocked", lambda slug, issue_id: False)

    assert "bad link" in await pt.link_issues_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "target_id": "i2", "relation": "blocks"})
    assert await pt.check_blocked_issues_tool.ainvoke({"product_slug": "app"}) == "No blocked issues found"


@pytest.mark.asyncio
async def test_manage_review_tool_all_actions(monkeypatch):
    review = {
        "id": "r1",
        "status": "open",
        "trigger": "manual",
        "trigger_ref": "x",
        "owner": "00010",
        "items": [{"key": "scope", "label": "Scope OK", "checked": False}],
    }
    monkeypatch.setattr(pt.prod, "list_reviews", lambda slug: [review])
    monkeypatch.setattr(pt.prod, "load_review", lambda slug, review_id: review if review_id == "r1" else None)
    monkeypatch.setattr(pt.prod, "update_review_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(pt.prod, "complete_review", lambda slug, review_id: {"completed_at": "now"})

    assert "r1" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "list"})
    assert "Scope OK" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "view", "review_id": "r1"})
    assert "not found" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "view", "review_id": "missing"})
    assert "review_id is required" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "view"})
    assert "item_key is required" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "check", "review_id": "r1"})
    assert "Checked item" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "check", "review_id": "r1", "item_key": "scope"})
    assert "Unchecked item" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "check", "review_id": "r1", "item_key": "scope", "checked": "false"})
    assert "completed at now" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "complete", "review_id": "r1"})
    assert "unknown action" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "bad", "review_id": "r1"})


@pytest.mark.asyncio
async def test_manage_review_no_reviews_and_error(monkeypatch):
    monkeypatch.setattr(pt.prod, "list_reviews", lambda slug: [])
    assert await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "list"}) == "No reviews found"

    def raise_value_error(*args, **kwargs):
        raise ValueError("review error")

    monkeypatch.setattr(pt.prod, "load_review", raise_value_error)
    assert "review error" in await pt.manage_review_tool.ainvoke({"product_slug": "app", "action": "view", "review_id": "r1"})


def test_sync_product_tools(monkeypatch):
    monkeypatch.setattr(pt.prod, "delete_issue", lambda slug, issue_id: None)
    monkeypatch.setattr(pt.prod, "reopen_issue", lambda slug, issue_id: {"status": "backlog"})
    monkeypatch.setattr(pt.prod, "start_sprint", lambda slug, sprint_id: {"name": "Sprint"})
    monkeypatch.setattr(pt.prod, "delete_sprint", lambda slug, sprint_id: None)
    monkeypatch.setattr(pt.prod, "load_sprint", lambda slug, sprint_id: {"name": "Sprint", "status": "active", "start_date": "2026-01-01", "end_date": "2026-01-14", "goal": "Ship"})
    monkeypatch.setattr(pt.prod, "get_sprint_velocity", lambda slug, sprint_id: 13)
    monkeypatch.setattr(pt.prod, "list_issues", lambda slug, sprint=None, **kwargs: [{"status": "done"}, {"status": "backlog"}])
    monkeypatch.setattr(pt.prod, "list_versions", lambda slug: [{"version": "1.0.0", "released_at": "today"}])
    monkeypatch.setattr(pt.prod, "release_version", lambda slug, ids, bump: {"version": "1.0.1"})

    assert "Deleted issue i1" in pt.delete_issue_tool.invoke({"product_slug": "app", "issue_id": "i1"})
    assert "Reopened issue i1" in pt.reopen_issue_tool.invoke({"product_slug": "app", "issue_id": "i1"})
    assert "Started sprint s1" in pt.start_sprint_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "Deleted sprint s1" in pt.delete_sprint_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "Velocity: 13" in pt.sprint_analytics_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "1.0.0" in pt.version_management_tool.invoke({"product_slug": "app", "action": "list"})
    assert "Released v1.0.1" in pt.version_management_tool.invoke({"product_slug": "app", "action": "release", "resolved_issue_ids": "i1,i2", "bump": "patch"})
    assert "unknown action" in pt.version_management_tool.invoke({"product_slug": "app", "action": "bad"})


def test_sync_product_tool_errors(monkeypatch):
    def raise_value_error(*args, **kwargs):
        raise ValueError("product error")

    monkeypatch.setattr(pt.prod, "delete_issue", raise_value_error)
    monkeypatch.setattr(pt.prod, "reopen_issue", raise_value_error)
    monkeypatch.setattr(pt.prod, "start_sprint", raise_value_error)
    monkeypatch.setattr(pt.prod, "delete_sprint", raise_value_error)
    monkeypatch.setattr(pt.prod, "load_sprint", lambda slug, sprint_id: None)
    monkeypatch.setattr(pt.prod, "list_versions", lambda slug: [])
    monkeypatch.setattr(pt.prod, "release_version", raise_value_error)

    assert "product error" in pt.delete_issue_tool.invoke({"product_slug": "app", "issue_id": "i1"})
    assert "product error" in pt.reopen_issue_tool.invoke({"product_slug": "app", "issue_id": "i1"})
    assert "product error" in pt.start_sprint_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "product error" in pt.delete_sprint_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "Sprint 's1' not found" in pt.sprint_analytics_tool.invoke({"product_slug": "app", "sprint_id": "s1"})
    assert "No versions released" in pt.version_management_tool.invoke({"product_slug": "app", "action": "list"})
    assert "product error" in pt.version_management_tool.invoke({"product_slug": "app", "action": "release", "resolved_issue_ids": "i1"})


@pytest.mark.asyncio
async def test_product_admin_tools(monkeypatch):
    monkeypatch.setattr(pt.prod, "update_product", lambda slug, **fields: {"slug": slug, **fields} if slug != "missing" else None)
    monkeypatch.setattr(pt.prod, "delete_product", lambda slug: {"issues": 2, "versions": 1, "projects": 3})
    monkeypatch.setattr(pt.prod, "update_issue", lambda slug, issue_id, **fields: {"id": issue_id, **fields})

    assert "no fields" in await pt.update_product_tool.ainvoke({"product_slug": "app"})
    assert "Updated product" in await pt.update_product_tool.ainvoke({"product_slug": "app", "name": "New", "description": "Desc"})
    assert "not found" in await pt.update_product_tool.ainvoke({"product_slug": "missing", "name": "New"})
    assert "Removed 2 issues" in await pt.delete_product_tool.ainvoke({"product_slug": "app"})
    assert "assigned to 00010" in await pt.assign_issue_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "assignee_id": "00010"})
    assert "ownership transferred" in await pt.transfer_product_ownership_tool.ainvoke({"product_slug": "app", "new_owner_id": "00011"})


@pytest.mark.asyncio
async def test_product_admin_tool_errors(monkeypatch):
    def raise_value_error(*args, **kwargs):
        raise ValueError("admin error")

    monkeypatch.setattr(pt.prod, "update_product", lambda slug, **fields: None if slug == "missing" else (_ for _ in ()).throw(ValueError("admin error")))
    monkeypatch.setattr(pt.prod, "delete_product", raise_value_error)
    monkeypatch.setattr(pt.prod, "update_issue", raise_value_error)

    assert "admin error" in await pt.update_product_tool.ainvoke({"product_slug": "app", "objective": "New"})
    assert "admin error" in await pt.delete_product_tool.ainvoke({"product_slug": "app"})
    assert "admin error" in await pt.assign_issue_tool.ainvoke({"product_slug": "app", "issue_id": "i1", "assignee_id": "00010"})
    assert "not found" in await pt.transfer_product_ownership_tool.ainvoke({"product_slug": "missing", "new_owner_id": "00011"})
