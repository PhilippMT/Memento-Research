from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from onemancompany.core.task_tree import TaskTree, register_tree
from onemancompany.core import pipeline_engine as pe


@pytest.fixture(autouse=True)
def clear_pipeline_registry():
    pe._active_pipelines.clear()
    yield
    pe._active_pipelines.clear()


def _employee_config(name: str, skills: list[str]) -> SimpleNamespace:
    return SimpleNamespace(name=name, skills=skills)


def test_state_round_trip_and_registry_reload(tmp_path):
    assert pe._load_state(str(tmp_path)) == {}

    state = {"topic": "graph RAG", "current_stage": 4, "phase": "gate"}
    pe._save_state(str(tmp_path), state)

    assert pe._load_state(str(tmp_path)) == state
    assert pe.get_or_load_pipeline("missing", str(tmp_path / "empty")) is None

    engine = pe.get_or_load_pipeline("p1", str(tmp_path))
    assert engine is pe.get_pipeline("p1")
    assert engine.state == state

    assert pe.get_or_load_pipeline("p1", str(tmp_path)) is engine


def test_find_employee_by_skill_uses_first_matching_config(monkeypatch):
    monkeypatch.setattr(
        pe,
        "load_employee_configs",
        lambda: {
            "00010": _employee_config("Writer", ["paper_writer"]),
            "00011": _employee_config("Reviewer", ["adversarial_review"]),
        },
    )

    assert pe._find_employee_by_skill("adversarial_review") == "00011"
    assert pe._find_employee_by_skill("missing") is None


def test_start_clamps_stage_uses_assignment_and_builds_context(tmp_path, monkeypatch):
    dispatched = []
    emitted = []

    def fake_dispatch(self, employee_id, description, title):
        dispatched.append((employee_id, description, title))
        self.state["active_node_id"] = "node-1"
        self.state["active_employee_id"] = employee_id

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", fake_dispatch)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: emitted.append((args, kwargs)))
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {"emp-9": _employee_config("Closer", ["peer_reviewer"])})

    engine = pe.PipelineEngine("p1", str(tmp_path), "causal discovery")
    engine.state["stage_results"] = {"1": "refined topic"}
    engine.start(
        start_stage=12,
        end_stage=0,
        prior_context="uploaded notes",
        stage_assignments={"9": "emp-9"},
    )

    assert engine.current_stage == 9
    assert engine.state["start_stage"] == 9
    assert engine.state["end_stage"] == 9
    assert dispatched[0][0] == "emp-9"
    assert dispatched[0][2] == "Stage 9: Self-Review"
    assert "uploaded notes" in dispatched[0][1]
    assert "refined topic" in dispatched[0][1]
    assert "stage9_peer_reviewer.md" in dispatched[0][1]
    assert emitted == [(("stage_start", 9), {"employee_name": "Closer", "employee_id": "emp-9"})]


def test_dispatch_producer_fails_when_no_employee(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_producer()

    assert engine.phase == "failed"


def test_dispatch_producer_with_feedback_uses_skill_lookup(tmp_path, monkeypatch):
    dispatched = []
    emitted = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-topic" if skill == "topic_refiner" else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: emitted.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_producer(feedback="tighten the framing")

    assert dispatched[0][0] == "emp-topic"
    assert "Feedback from previous review" in dispatched[0][1]
    assert "tighten the framing" in dispatched[0][1]
    assert emitted == [(("stage_start", 1), {"employee_name": "emp-topic", "employee_id": "emp-topic"})]


def test_dispatch_to_employee_uses_ea_child_as_parent_and_schedules(tmp_path, monkeypatch):
    scheduled = []

    class FakeManager:
        def schedule_node(self, employee_id, node_id, tree_path):
            scheduled.append(("schedule", employee_id, node_id, tree_path))

        def _schedule_next(self, employee_id):
            scheduled.append(("next", employee_id))

    import onemancompany.core.agent_loop as agent_loop

    monkeypatch.setattr(agent_loop, "employee_manager", FakeManager())

    tree = TaskTree("p1")
    root = tree.create_root("00001", "CEO request")
    ea_node = tree.add_child(root.id, "00004", "EA coordination", [])
    tree_path = tmp_path / "task_tree.yaml"
    register_tree(tree_path, tree)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_to_employee("00015", "do the work", "Stage 1")

    node = tree.get_node(engine.state["active_node_id"])
    assert node.parent_id == ea_node.id
    assert node.employee_id == "00015"
    assert node.title == "Stage 1"
    assert node.metadata["pipeline_managed"] is True
    assert scheduled[0] == ("schedule", "00015", node.id, str(tree_path))
    assert scheduled[1] == ("next", "00015")


def test_producer_completion_stores_result_and_dispatches_critic(tmp_path, monkeypatch):
    calls = []
    emitted = []

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_critic", lambda self, result: calls.append(result))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: emitted.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.on_task_complete("emp", "node", "producer output")

    assert engine.state["stage_results"]["1"] == "producer output"
    assert calls == ["producer output"]
    assert emitted == [(("stage_reviewing", 1), {})]


def test_critic_completion_pass_moves_to_gate(tmp_path, monkeypatch):
    critic_events = []
    stage_events = []
    gate_events = []

    monkeypatch.setattr(pe.PipelineEngine, "_emit_critic_result", lambda self, *args, **kwargs: critic_events.append((args, kwargs)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: stage_events.append((args, kwargs)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_gate_event", lambda self, *args, **kwargs: gate_events.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["phase"] = "critic"
    engine.state["stage_results"] = {"1": "producer output"}
    engine.on_task_complete("critic", "node", "PASS\nConfidence Score: 0.82")

    assert engine.phase == "gate"
    assert engine.state["critic_result"].startswith("PASS")
    assert critic_events == [((1, "PASS\nConfidence Score: 0.82", True, 0.82), {})]
    assert stage_events == [(("stage_complete", 1), {"confidence": 0.82})]
    assert gate_events == [((1, 0.82), {})]


def test_critic_reject_retries_with_feedback(tmp_path, monkeypatch):
    producer_feedback = []
    stage_events = []
    critic_events = []

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_producer", lambda self, feedback="": producer_feedback.append(feedback))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: stage_events.append((args, kwargs)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_critic_result", lambda self, *args, **kwargs: critic_events.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["phase"] = "critic"
    engine.on_task_complete("critic", "node", "REJECT\nconfidence: 0.41\nNeeds tighter scope")

    assert engine.state["retries"] == 1
    assert producer_feedback == ["REJECT\nconfidence: 0.41\nNeeds tighter scope"]
    assert stage_events == [(("stage_failed", 1), {"confidence": 0.41})]
    assert critic_events == [((1, "REJECT\nconfidence: 0.41\nNeeds tighter scope", False, 0.41), {})]


def test_critic_reject_exhausted_waits_for_ceo(tmp_path, monkeypatch):
    gate_events = []
    monkeypatch.setattr(pe.PipelineEngine, "_emit_critic_result", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_gate_event", lambda self, *args, **kwargs: gate_events.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["phase"] = "critic"
    engine.state["retries"] = pe.MAX_RETRIES
    engine.on_task_complete("critic", "node", "REJECT confidence: 0.2")

    assert engine.phase == "gate"
    assert gate_events == [((1, 0.2), {"exhausted": True})]


def test_dispatch_critic_without_critic_auto_passes(tmp_path, monkeypatch):
    stage_events = []
    gate_events = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: stage_events.append((args, kwargs)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_gate_event", lambda self, *args, **kwargs: gate_events.append((args, kwargs)))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_critic("producer output")

    assert engine.phase == "gate"
    assert stage_events == [(("stage_complete", 1), {"confidence": None})]
    assert gate_events == [((1, None), {})]


def test_dispatch_critic_sends_review_task_to_critic(tmp_path, monkeypatch):
    dispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "critic-1" if skill == pe.CRITIC_SKILL else None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda self, *args: dispatched.append(args))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_critic("producer output")

    assert engine.phase == "critic"
    assert dispatched[0][0] == "critic-1"
    assert "Gate Review: Stage 1" in dispatched[0][1]
    assert "--- Producer Output ---\nproducer output" in dispatched[0][1]
    assert dispatched[0][2] == "Gate Review: Stage 1"


def test_ceo_approval_revision_advance_and_complete(tmp_path, monkeypatch):
    producer_feedback = []
    completed = []

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_producer", lambda self, feedback="": producer_feedback.append((self.current_stage, feedback)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_pipeline_complete", lambda self: completed.append(self.project_id))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 2
    engine.state["end_stage"] = 3
    engine.state["retries"] = 2
    engine.state["critic_result"] = "old"

    engine.on_ceo_approve("please REVISE the method")
    assert engine.state["retries"] == 0
    assert producer_feedback == [(2, "please REVISE the method")]

    engine.on_ceo_approve()
    assert engine.current_stage == 3
    assert engine.state["critic_result"] is None
    assert producer_feedback[-1] == (3, "")

    engine.on_ceo_approve()
    assert engine.phase == "done"
    assert completed == ["p1"]


def test_parse_critic_decision_and_confidence():
    assert pe.PipelineEngine._parse_critic_pass("reject: weak evidence") is False
    assert pe.PipelineEngine._parse_critic_pass("pass: strong enough") is True
    assert pe.PipelineEngine._parse_critic_pass("looks fine") is True
    assert pe.PipelineEngine._parse_confidence("Confidence: 1.0") == 1.0
    assert pe.PipelineEngine._parse_confidence("no score") is None


def test_parse_confidence_handles_unparseable_match(monkeypatch):
    class BadMatch:
        def group(self, index):
            assert index == 1
            return "bad"

    import re

    monkeypatch.setattr(re, "search", lambda *args, **kwargs: BadMatch())

    assert pe.PipelineEngine._parse_confidence("confidence: bad") is None


def test_event_emitters_skip_when_no_running_loop(tmp_path):
    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["stage_results"] = {"1": "done"}

    engine._emit_critic_result(1, "REJECT", False)
    engine._emit_stage_event("stage_complete", 1, confidence=0.5)
    engine._emit_gate_event(1, 0.5)
    engine._emit_pipeline_complete()


@pytest.mark.asyncio
async def test_event_emitters_publish_payloads_in_running_loop(tmp_path, monkeypatch):
    published = []

    async def fake_publish(event):
        published.append(event)

    monkeypatch.setattr(pe.event_bus, "publish", fake_publish)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["stage_results"] = {"1": "done"}

    await engine._emit_async({"type": "direct"})
    engine._emit_critic_result(1, "PASS confidence: 0.7", True, 0.7)
    engine._emit_stage_event("stage_start", 1, employee_name="Analyst", employee_id="00015")
    engine._emit_gate_event(1, 0.7, exhausted=True)
    engine._emit_pipeline_complete()
    await asyncio.sleep(0)

    payloads = [event.payload for event in published]
    assert payloads[0] == {"type": "direct"}
    assert payloads[1]["type"] == "critic_result"
    assert payloads[1]["decision"] == "PASS"
    assert payloads[2]["type"] == "stage_start"
    assert payloads[2]["employee_name"] == "Analyst"
    assert payloads[3]["type"] == "breakpoint_hit"
    assert payloads[3]["retries_exhausted"] is True
    assert payloads[4] == {"type": "pipeline_complete", "project_id": "p1", "stages_completed": 1, "pipeline_managed": True}


def test_dispatch_producer_stage4_injects_methodology_debate_skill_trigger(tmp_path, monkeypatch):
    """Stage 4 (Methodology Design) task description must instruct the
    methodology_designer to load the methodology-debate-convener skill
    before producing the deliverable. Other stages must not get this trigger."""
    dispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "emp-meth" if skill == "methodology_designer" else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine._dispatch_producer()

    assert dispatched, "producer must dispatch"
    desc = dispatched[0][1]
    assert 'load_skill("methodology-debate-convener")' in desc, (
        "Stage 4 task description must instruct the producer to load the convener skill"
    )
    # Preamble must describe the draft → debate → revise flow, not the
    # pre-#19 "synthesise transcript into methodology document" wording.
    assert "draft" in desc.lower() and "revise" in desc.lower(), (
        "Stage 4 trigger preamble must mention the draft → debate → revise flow"
    )


def test_dispatch_producer_non_stage4_does_not_inject_debate_skill(tmp_path, monkeypatch):
    """Stages other than 4 must not contain the methodology-debate-convener trigger."""
    dispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "emp-topic" if skill == "topic_refiner" else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 1
    engine._dispatch_producer()

    assert dispatched, "producer must dispatch"
    desc = dispatched[0][1]
    assert "methodology-debate-convener" not in desc, (
        "Non-Stage-4 stages must not carry the debate convener trigger"
    )


def test_dispatch_critic_stage4_injects_methodology_quality_critic_skill(tmp_path, monkeypatch):
    """Stage 4 critic dispatch must instruct the reviewer to load the
    methodology-quality-critic skill, which enforces CCF-A grade criteria.
    Other stages' critic dispatches must not get this directive."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "critic-1" if skill == pe.CRITIC_SKILL else None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine._dispatch_critic("draft methodology document")

    assert dispatched, "critic must be dispatched"
    desc = dispatched[0][1]
    assert 'load_skill("methodology-quality-critic")' in desc, (
        "Stage 4 critic description must instruct the reviewer to load the quality-critic skill"
    )


def test_dispatch_critic_non_stage4_does_not_inject_quality_critic(tmp_path, monkeypatch):
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "critic-1" if skill == pe.CRITIC_SKILL else None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5  # Experiment Design
    engine._dispatch_critic("experiment plan output")

    assert dispatched, "critic must be dispatched"
    desc = dispatched[0][1]
    assert "methodology-quality-critic" not in desc, (
        "Non-Stage-4 critic dispatch must not carry the methodology critic skill trigger"
    )


def test_dispatch_producer_stage5_injects_experiment_debate_skill_trigger(tmp_path, monkeypatch):
    """Stage 5 (Experiment Design) task description must instruct the
    producer to load the experiment-debate-convener skill before designing."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "emp-exp" if skill == "experiment_designer" else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5
    engine._dispatch_producer()

    assert dispatched, "producer must dispatch"
    desc = dispatched[0][1]
    assert 'load_skill("experiment-debate-convener")' in desc, (
        "Stage 5 task description must instruct the producer to load the experiment convener skill"
    )


def test_dispatch_critic_stage5_injects_experiment_quality_critic_skill(tmp_path, monkeypatch):
    """Stage 5 critic dispatch must instruct the reviewer to load the
    experiment-quality-critic skill."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "critic-1" if skill == pe.CRITIC_SKILL else None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5
    engine._dispatch_critic("draft experiment plan")

    assert dispatched, "critic must be dispatched"
    desc = dispatched[0][1]
    assert 'load_skill("experiment-quality-critic")' in desc, (
        "Stage 5 critic description must instruct the reviewer to load the experiment quality-critic skill"
    )


def test_dispatch_producer_stage5_trigger_not_in_stage4_or_other(tmp_path, monkeypatch):
    """Triggers should be stage-id-scoped — Stage 5 trigger must not appear
    in Stage 4 producer (which has its own methodology trigger) or Stage 1."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: f"emp-{skill}" if skill else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    for stage_id in (1, 4):
        dispatched.clear()
        engine = pe.PipelineEngine(f"p{stage_id}", str(tmp_path), "topic")
        engine.state["current_stage"] = stage_id
        engine._dispatch_producer()
        if dispatched:
            assert "experiment-debate-convener" not in dispatched[0][1]


# ---------------------------------------------------------------------------
# Stage 6 (Auto Experiment) — runner preference + autoresearch trigger
# ---------------------------------------------------------------------------

def test_find_employee_for_stage_6_prefers_runner_over_experimentalist(monkeypatch):
    """When both an experiment_runner and an experimentalist are on the
    roster, Stage 6 must route to the runner — they hold the autoresearch
    runbook and can actually fire remote infra."""
    by_skill = {
        "experiment_runner": "emp-runner-007",
        "experimentalist": "emp-sim-001",
    }
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda s: by_skill.get(s))
    assert pe._find_employee_for_stage(6, "experimentalist") == "emp-runner-007"


def test_find_employee_for_stage_6_falls_back_to_experimentalist(monkeypatch):
    """No experiment_runner on roster — Stage 6 falls back to the existing
    experimentalist so the pipeline still runs (degraded, simulation only)."""
    by_skill = {"experimentalist": "emp-sim-001"}
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda s: by_skill.get(s))
    assert pe._find_employee_for_stage(6, "experimentalist") == "emp-sim-001"


def test_find_employee_for_stage_5_unchanged_no_runner_fallback(monkeypatch):
    """Runner fallback is Stage 6 only — Stage 5 must keep using the
    primary skill (experiment_designer) so we don't accidentally swap
    who writes the experiment plan."""
    by_skill = {
        "experiment_runner": "emp-runner-007",
        "experiment_designer": "emp-designer-006",
    }
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda s: by_skill.get(s))
    assert pe._find_employee_for_stage(5, "experiment_designer") == "emp-designer-006"


def test_dispatch_producer_stage6_injects_execution_runbook_trigger(tmp_path, monkeypatch):
    """Stage 6 producer task description must instruct the agent to load
    the experiment-execution-runbook skill (which itself names autoresearch)."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "emp-runner-007" if skill == "experiment_runner" else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 6
    engine._dispatch_producer()

    assert dispatched, "Stage 6 producer must dispatch"
    desc = dispatched[0][1]
    assert 'load_skill("experiment-execution-runbook")' in desc, (
        "Stage 6 task description must instruct the producer to load the execution runbook"
    )
    assert "autoresearch" in desc, (
        "Stage 6 task description must mention the autoresearch runbook so the "
        "agent knows the fast_*.sh scripts are available for remote rows"
    )


def test_dispatch_producer_stage6_routes_to_experiment_runner_employee(tmp_path, monkeypatch):
    """Stage 6 dispatch resolution must reach the experiment_runner
    employee (not whoever owns the 'experimentalist' skill) when a runner
    is on the roster."""
    dispatched = []
    monkeypatch.setattr(
        pe, "_find_employee_by_skill",
        lambda skill: {"experiment_runner": "emp-runner",
                       "experimentalist": "emp-sim"}.get(skill),
    )
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, emp_id, *rest: dispatched.append(emp_id))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 6
    engine._dispatch_producer()

    assert dispatched == ["emp-runner"], (
        f"Expected dispatch to experiment_runner employee, got {dispatched}"
    )


def test_dispatch_critic_stage6_injects_evidence_grading(tmp_path, monkeypatch):
    """Stage 6 critic dispatch must instruct the reviewer to verify real
    run_ids + cost + log_tail — fabricated results are auto-REJECT."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: "critic-1" if skill == pe.CRITIC_SKILL else None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 6
    engine._dispatch_critic("Stage 6 producer report")

    assert dispatched, "Stage 6 critic must be dispatched"
    desc = dispatched[0][1]
    assert "run_id" in desc, (
        "Stage 6 critic prompt must require evidence — a real run_id"
    )
    assert "auto-REJECT" in desc or "fabricat" in desc.lower(), (
        "Stage 6 critic prompt must explicitly call out fabricated results"
    )


def test_stage6_trigger_not_in_stage4_or_stage5(tmp_path, monkeypatch):
    """Stage 6 triggers (execution-runbook + run_id grading) must be
    stage-scoped — earlier stages must not carry them."""
    dispatched = []
    monkeypatch.setattr(pe, "_find_employee_by_skill",
                        lambda skill: f"emp-{skill}" if skill else None)
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee",
                        lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event",
                        lambda self, *args, **kwargs: None)

    for stage_id in (4, 5):
        dispatched.clear()
        engine = pe.PipelineEngine(f"p{stage_id}", str(tmp_path), "topic")
        engine.state["current_stage"] = stage_id
        engine._dispatch_producer()
        if dispatched:
            assert "experiment-execution-runbook" not in dispatched[0][1]
