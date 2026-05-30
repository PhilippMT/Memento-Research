"""``PUT /api/employee/{id}/llm`` and ``GET /api/employee/{id}/hire-defaults``
let the employee settings panel edit temperature + api_key (the two fields
the existing /model + /auth/apply path didn't surface) atomically, and show
the original hire_list defaults next to the override so the user knows what
the baseline is."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


def _make_app():
    from fastapi import FastAPI
    from onemancompany.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _emp_payload(emp_id: str = "00006", *, talent_id: str = "topic-refiner") -> dict:
    return {
        "id": emp_id, "employee_number": emp_id, "name": "Topic Refiner",
        "hosting": "company", "llm_model": "old/model", "api_provider": "openrouter",
        "temperature": 0.7, "api_key": "", "skills": ["topic_refiner"],
        "salary_per_1m_tokens": 1.0, "talent_id": talent_id, "level": 1, "role": "Researcher",
    }


# ---------------------------------------------------------------------------
# PUT /api/employee/{id}/llm
# ---------------------------------------------------------------------------

class TestUpdateEmployeeLlm:
    @pytest.fixture
    def patched(self, monkeypatch):
        """Patch out the side-effects (store I/O, agent rebuild, event bus)."""
        saved = {}
        cfg = MagicMock()
        cfg.hosting = "company"
        cfg.api_provider = "openrouter"
        cfg.llm_model = "old/model"
        cfg.salary_per_1m_tokens = 1.0
        cfg.temperature = 0.7
        cfg.api_key = ""

        rebuilt = []
        published = []

        async def fake_save(emp_id, data):
            saved.setdefault(emp_id, {}).update(data)

        from onemancompany.api import routes
        monkeypatch.setattr(routes, "_load_emp", lambda eid: _emp_payload(eid))
        monkeypatch.setattr(routes._store, "save_employee", fake_save)
        monkeypatch.setattr(routes, "_rebuild_employee_agent",
                            lambda eid: rebuilt.append(eid) or True)
        monkeypatch.setattr(routes.event_bus, "publish",
                            AsyncMock(side_effect=lambda e: published.append(e)))
        monkeypatch.setattr(
            "onemancompany.core.config.employee_configs",
            {"00006": cfg},
        )
        return {"saved": saved, "cfg": cfg, "rebuilt": rebuilt, "published": published}

    async def test_atomic_update_of_all_four_fields(self, patched):
        app = _make_app()
        body = {
            "model": "anthropic/claude-opus-4.8-fast",
            "api_provider": "openrouter",
            "api_key": "sk-new-secret",
            "temperature": 0.25,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.put("/api/employee/00006/llm", json=body)
        assert resp.status_code == 200, resp.text
        saved = patched["saved"]["00006"]
        assert saved["llm_model"] == "anthropic/claude-opus-4.8-fast"
        assert saved["api_provider"] == "openrouter"
        assert saved["api_key"] == "sk-new-secret"
        assert saved["temperature"] == 0.25
        # In-memory cfg also reflects the new values for the next dispatch.
        assert patched["cfg"].llm_model == "anthropic/claude-opus-4.8-fast"
        assert patched["cfg"].temperature == 0.25
        assert patched["cfg"].api_key == "sk-new-secret"
        # Agent was rebuilt so the next task uses the new LLM.
        assert patched["rebuilt"] == ["00006"]

    async def test_partial_update_only_temperature_leaves_others_alone(self, patched):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.put("/api/employee/00006/llm", json={"temperature": 0.4})
        assert resp.status_code == 200
        saved = patched["saved"]["00006"]
        # Only temperature was persisted; we don't write fields the caller
        # didn't send, so unrelated cfg stays at its prior value.
        assert saved == {"temperature": 0.4}
        assert patched["cfg"].temperature == 0.4
        assert patched["cfg"].llm_model == "old/model"  # untouched

    async def test_temperature_out_of_range_returns_400(self, patched):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.put("/api/employee/00006/llm", json={"temperature": 5})
        assert resp.status_code == 400
        # Nothing was persisted or rebuilt.
        assert "00006" not in patched["saved"]
        assert patched["rebuilt"] == []

    async def test_empty_body_returns_400(self, patched):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.put("/api/employee/00006/llm", json={})
        assert resp.status_code == 400

    async def test_unknown_employee_returns_404(self, patched, monkeypatch):
        from onemancompany.api import routes
        monkeypatch.setattr(routes, "_load_emp", lambda eid: None)
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.put("/api/employee/99999/llm", json={"temperature": 0.5})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/employee/{id}/hire-defaults
# ---------------------------------------------------------------------------

class TestEmployeeHireDefaults:
    @pytest.fixture
    def patched_hire_list(self, monkeypatch, tmp_path):
        """Point DATA_ROOT at a temp dir with a stub hire_list.json."""
        import json
        company_dir = tmp_path / "company"
        company_dir.mkdir()
        (company_dir / "hire_list.json").write_text(json.dumps([
            {
                "talent_id": "topic-refiner",
                "name": "Topic Refiner",
                "llm_model": "MiniMax-M2.7",
                "api_provider": "custom",
                "temperature": 0.3,
            },
        ]), encoding="utf-8")
        from onemancompany.api import routes
        monkeypatch.setattr(routes, "DATA_ROOT", tmp_path, raising=False)
        return tmp_path

    async def test_returns_hire_list_entry_matching_talent_id(self, patched_hire_list, monkeypatch):
        from onemancompany.api import routes
        monkeypatch.setattr(routes, "_load_emp", lambda eid: _emp_payload(eid, talent_id="topic-refiner"))
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/employee/00006/hire-defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_model"] == "MiniMax-M2.7"
        assert data["api_provider"] == "custom"
        assert data["temperature"] == 0.3

    async def test_returns_404_when_employee_has_no_talent_id(self, patched_hire_list, monkeypatch):
        from onemancompany.api import routes
        monkeypatch.setattr(routes, "_load_emp", lambda eid: _emp_payload(eid, talent_id=""))
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/employee/00006/hire-defaults")
        assert resp.status_code == 404

    async def test_returns_404_when_talent_id_not_in_hire_list(self, patched_hire_list, monkeypatch):
        from onemancompany.api import routes
        monkeypatch.setattr(routes, "_load_emp", lambda eid: _emp_payload(eid, talent_id="ghost"))
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/employee/00006/hire-defaults")
        assert resp.status_code == 404
