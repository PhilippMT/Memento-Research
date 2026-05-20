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


def test_queue_pending_feedback_appends_and_persists(tmp_path):
    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.queue_pending_feedback("first hint")
    engine.queue_pending_feedback("second hint")
    assert "first hint" in engine.state["pending_user_feedback"]
    assert "second hint" in engine.state["pending_user_feedback"]

    # Reload from disk → still there
    reloaded = pe._load_state(str(tmp_path))
    assert "first hint" in reloaded["pending_user_feedback"]
    assert "second hint" in reloaded["pending_user_feedback"]


def test_dispatch_producer_consumes_pending_user_feedback(tmp_path, monkeypatch):
    dispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-topic")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.queue_pending_feedback("按意见整改")
    engine._dispatch_producer(feedback="critic says shorten")

    # Both critic feedback and queued CEO feedback land in the prompt.
    desc = dispatched[0][1]
    assert "shorten" in desc
    assert "按意见整改" in desc
    # Pending feedback is consumed after dispatch (single-use).
    assert engine.state.get("pending_user_feedback", "") == ""


def test_dispatch_producer_without_pending_feedback_unchanged(tmp_path, monkeypatch):
    dispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-topic")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda self, *args: dispatched.append(args))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._dispatch_producer()

    desc = dispatched[0][1]
    assert "Direct guidance from CEO" not in desc
    assert "pending_user_feedback" not in engine.state or engine.state.get("pending_user_feedback", "") == ""


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


@pytest.mark.parametrize("feedback,expect_revise", [
    # advance-with-comment chats that must NOT trigger a redo
    ("再补充一点细节", False),
    ("再讨论一下这个点", False),
    ("可以修改一下措辞", False),
    ("再加一个 baseline", False),
    # explicit redo triggers
    ("重新写 stage 4", True),
    ("重做这部分", True),
    ("please REVISE the methodology", True),
    ("Let's redo this stage", True),
    ("再写一遍 introduction", True),
])
def test_on_ceo_approve_revision_keyword_matching(tmp_path, monkeypatch, feedback, expect_revise):
    """Narrow keyword matcher: single-char '再' / ambiguous '修改' must not
    trigger a redo on otherwise benign CEO chat. Explicit multi-char redo
    triggers should fire."""
    redispatched = []
    advanced = []

    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_producer", lambda self, feedback="": redispatched.append((self.current_stage, feedback)))
    monkeypatch.setattr(pe.PipelineEngine, "_emit_pipeline_complete", lambda self: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 2
    engine.state["end_stage"] = 9
    initial_stage = engine.current_stage

    engine.on_ceo_approve(feedback)

    if expect_revise:
        # revise path: same stage, _dispatch_producer called with feedback
        assert redispatched and redispatched[-1][0] == initial_stage
        assert engine.state["retries"] == 0
    else:
        # advance path: stage advanced, no producer redispatch with feedback
        assert engine.current_stage == initial_stage + 1
        # _dispatch_producer is called on advance too (for the new stage) — feedback should be empty
        assert all(fb == "" for _, fb in redispatched), f"unexpected redispatch with feedback: {redispatched}"


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


# ---------------------------------------------------------------------------
# on_task_failed — producer failure handling (PR #34)
# ---------------------------------------------------------------------------


def test_on_task_failed_critic_phase_auto_passes(tmp_path, monkeypatch):
    """A FAILED *critic* must not re-dispatch the producer (which would
    discard the existing producer output and double-bill tokens). Mirrors
    the "no critic employee found" branch in _dispatch_critic by
    auto-passing on the stored producer output."""
    on_pass_calls = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-meth")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(
        pe.PipelineEngine, "_dispatch_to_employee",
        lambda self, *args: pytest.fail("must not re-dispatch on critic failure"),
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_stage_event",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_gate_event",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_on_critic_pass",
        lambda self, result, confidence=None: on_pass_calls.append((result, confidence)),
    )

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine.state["phase"] = "critic"
    engine.state["stage_results"]["4"] = "producer output for stage 4"

    engine.on_task_failed("critic-emp", "node-x", "critic crashed: OOM")

    assert on_pass_calls == [("producer output for stage 4", None)]
    assert engine.state.get("retries", 0) == 0, "retries must not increment on critic failure"


def test_on_task_failed_retries_until_exhausted(tmp_path, monkeypatch):
    """A FAILED producer is treated like a critic REJECT: retry up to
    MAX_RETRIES, then hold the gate for CEO. Crucially, the failure must
    NOT fall through to vessel.py's legacy completion check (which would
    misdeclare the project done)."""
    dispatched = []
    emitted = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-meth")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(
        pe.PipelineEngine, "_dispatch_to_employee",
        lambda self, *args: dispatched.append(args),
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_stage_event",
        lambda self, *args, **kwargs: None,
    )
    monkeypatch.setattr(
        pe.PipelineEngine, "_emit_gate_event",
        lambda self, *args, **kwargs: emitted.append(("gate", args, kwargs)),
    )

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine.state["phase"] = "producer"

    # First failure: retry 1
    engine.on_task_failed("emp-meth", "node-a", "TypeError: …")
    assert engine.state["retries"] == 1
    assert engine.state["phase"] == "producer"
    assert dispatched, "should re-dispatch producer"
    assert "TypeError" in dispatched[-1][1]

    # Second failure: retry 2
    engine.on_task_failed("emp-meth", "node-b", "OOM")
    assert engine.state["retries"] == 2

    # Third failure: retry 3
    engine.on_task_failed("emp-meth", "node-c", "timeout")
    assert engine.state["retries"] == 3

    # Fourth failure: retries exhausted → gate, no new dispatch
    redispatch_count_before = len(dispatched)
    engine.on_task_failed("emp-meth", "node-d", "still broken")
    assert engine.state["phase"] == "gate"
    assert len(dispatched) == redispatch_count_before, "must not re-dispatch when exhausted"
    assert any(kind == "gate" for kind, _, _ in emitted)


def test_emit_pipeline_complete_marks_ceo_root_finished(tmp_path, monkeypatch):
    """When the pipeline truly completes (after stage 9 / end_stage), the
    engine must close the CEO root so the UI's project-complete affordance
    fires here and only here. Previously the legacy EA-anchor heuristic
    in vessel.py closed the root after Stage 1, before the pipeline was
    actually done."""
    from onemancompany.core.task_tree import TaskTree, register_tree, get_tree
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase
    from onemancompany.core.config import TASK_TREE_FILENAME

    tree = TaskTree(project_id="p1")
    root = tree.create_root("00001", "do research")
    root.node_type = NodeType.CEO_PROMPT
    root.set_status(TaskPhase.PROCESSING)
    tree_path = tmp_path / TASK_TREE_FILENAME
    tree.save(tree_path)
    register_tree(tree_path, tree)

    monkeypatch.setattr(pe.PipelineEngine, "_emit_async", lambda self, payload: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._emit_pipeline_complete()

    root2 = tree.get_node(tree.root_id)
    assert root2.status == TaskPhase.FINISHED.value


@pytest.mark.parametrize("starting_status", ["failed", "blocked", "cancelled"])
def test_emit_pipeline_complete_skips_finalize_on_illegal_source(tmp_path, monkeypatch, starting_status):
    """If the CEO root is in a state from which COMPLETED is illegal
    (FAILED / BLOCKED / CANCELLED), ``_mark_ceo_root_finished`` must
    log-and-bail instead of crashing with IllegalTransitionError. The
    pipeline_complete event still emits — the root simply isn't walked
    further."""
    from onemancompany.core.task_tree import TaskTree, register_tree
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase
    from onemancompany.core.config import TASK_TREE_FILENAME

    tree = TaskTree(project_id="p1")
    root = tree.create_root("00001", "do research")
    root.node_type = NodeType.CEO_PROMPT
    # Walk through legal transitions to reach the requested terminal-ish state.
    if starting_status == "failed":
        root.set_status(TaskPhase.PROCESSING)
        root.set_status(TaskPhase.FAILED)
    elif starting_status == "blocked":
        root.set_status(TaskPhase.BLOCKED)
    elif starting_status == "cancelled":
        root.set_status(TaskPhase.CANCELLED)
    tree_path = tmp_path / TASK_TREE_FILENAME
    tree.save(tree_path)
    register_tree(tree_path, tree)

    monkeypatch.setattr(pe.PipelineEngine, "_emit_async", lambda self, payload: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._emit_pipeline_complete()  # must NOT raise IllegalTransitionError

    # Root stays in its original status — not coerced through illegal path.
    root_after = tree.get_node(tree.root_id)
    assert root_after.status == starting_status


def test_emit_pipeline_complete_idempotent_on_already_finished_root(tmp_path, monkeypatch):
    """If the CEO root is already FINISHED (e.g. via a prior call), the
    second emit must not raise or attempt illegal status transitions."""
    from onemancompany.core.task_tree import TaskTree, register_tree
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase
    from onemancompany.core.config import TASK_TREE_FILENAME

    tree = TaskTree(project_id="p1")
    root = tree.create_root("00001", "do research")
    root.node_type = NodeType.CEO_PROMPT
    root.set_status(TaskPhase.PROCESSING)
    root.set_status(TaskPhase.COMPLETED)
    root.set_status(TaskPhase.ACCEPTED)
    root.set_status(TaskPhase.FINISHED)
    tree_path = tmp_path / TASK_TREE_FILENAME
    tree.save(tree_path)
    register_tree(tree_path, tree)

    monkeypatch.setattr(pe.PipelineEngine, "_emit_async", lambda self, payload: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine._emit_pipeline_complete()  # must not raise


# ---------------------------------------------------------------------------
# revert_to_stage — git-backed checkpoint + new instructions + re-run
# ---------------------------------------------------------------------------


def test_start_calls_ensure_initialized(tmp_path, monkeypatch):
    """Engine.start should auto-init a git repo in the workspace so
    subsequent commit_stage calls have somewhere to commit."""
    init_calls = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda self, *args: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: None)

    from onemancompany.core import project_repo
    monkeypatch.setattr(
        project_repo, "ensure_initialized",
        lambda repo_dir, iteration: init_calls.append((repo_dir, iteration)),
    )

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.start(start_stage=1, end_stage=3)

    assert init_calls, "engine.start must call project_repo.ensure_initialized"
    assert init_calls[0][0] == str(tmp_path)


def test_on_critic_pass_commits_and_tags_stage(tmp_path, monkeypatch):
    """After a stage passes, the engine commits the workspace and tags
    the commit so the user can later revert here."""
    commit_calls = []

    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_gate_event", lambda self, *args, **kwargs: None)

    from onemancompany.core import project_repo
    def _fake_commit(repo_dir, iteration, stage, stage_name):
        commit_calls.append((repo_dir, iteration, stage, stage_name))
        return "deadbeef"
    monkeypatch.setattr(project_repo, "commit_stage", _fake_commit)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 3
    engine._on_critic_pass("stage 3 output", confidence=0.9)

    assert commit_calls, "critic-pass must trigger commit_stage"
    repo_dir, iteration, stage, stage_name = commit_calls[0]
    assert repo_dir == str(tmp_path)
    assert stage == 3
    assert "Idea Generation" in stage_name  # STAGES[2].name


@pytest.mark.asyncio
async def test_revert_to_stage_checkouts_branch_and_redispatches(tmp_path, monkeypatch):
    """revert_to_stage(N, instructions) at a gate should:
       1. Create a feature branch rooted at the previous stage's tag.
       2. Reload state from disk (checkout flipped the file).
       3. Set current_stage=N, phase=producer, retries=0.
       4. Queue the user's instructions for stage N's producer.
       5. Re-dispatch.
       6. NOT scrub the workspace (no active task, no partial writes).
    """
    checkout_calls = []
    redispatched = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(
        pe.PipelineEngine, "_dispatch_to_employee",
        lambda self, eid, desc, title: redispatched.append((eid, desc, title)),
    )
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda self, *args, **kwargs: None)

    from onemancompany.core import project_repo
    def fake_checkout(repo_dir, iteration, stage, branch_name=None):
        checkout_calls.append((repo_dir, iteration, stage, branch_name))
        # Simulate the disk-side effect of a checkout: write a fresh
        # pipeline_state.yaml as if it had been restored from the prior tag.
        from pathlib import Path
        import yaml
        prior_state = {
            "topic": "topic",
            "current_stage": 2,
            "phase": "gate",
            "stage_results": {"1": "stage 1 result"},
            "retries": 0,
            "end_stage": 9,
        }
        (Path(repo_dir) / pe.STATE_FILENAME).write_text(yaml.safe_dump(prior_state))
        return branch_name or "feat-stage3-abcdef"
    monkeypatch.setattr(project_repo, "checkout_branch_from_stage", fake_checkout)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 7
    engine.state["phase"] = "gate"
    engine.state["retries"] = 2
    engine.state["stage_results"] = {
        "1": "...", "2": "...", "3": "...", "4": "...", "5": "...", "6": "...",
    }
    engine._save()

    branch = await engine.revert_to_stage(stage=3, instructions="please use H2O instead")

    assert checkout_calls, "checkout_branch_from_stage must be called"
    assert checkout_calls[0][2] == 3, "checkout target must be the stage being reverted to"
    assert branch == "feat-stage3-abcdef"

    # State was reloaded from disk (current_stage=2 came from disk), then
    # advanced to the revert target (3), with phase reset to producer.
    assert engine.state["current_stage"] == 3
    assert engine.state["phase"] == "producer"
    assert engine.state["retries"] == 0
    # Stale fields from the pre-revert state should be cleared.
    assert engine.state.get("critic_result") in (None, "")
    # Producer was re-dispatched, and the instructions reached its prompt
    # via the pending-feedback queue → _consume_pending_feedback drain.
    assert redispatched, "producer must be re-dispatched after revert"
    _eid, desc, _title = redispatched[0]
    assert "please use H2O instead" in desc, (
        "user's revert instructions must appear in the producer's prompt"
    )
    # Queue is drained after dispatch (single-use).
    assert engine.state.get("pending_user_feedback", "") == ""


def _stub_revert_environment(monkeypatch, *, restored_state: dict, branch: str = "feat-revert-xx"):
    """Common monkeypatches for the revert tests.

    Returns a dict whose keys record observable side effects: ``aborted``,
    ``waited`` (asyncio.Task handles whose .done() was awaited),
    ``discarded`` (repo paths scrubbed), ``checkout_calls``.
    """
    aborted: list[str] = []
    discarded: list[str] = []
    checkout_calls: list[tuple] = []

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda *a, **k: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda *a, **k: None)

    from onemancompany.core import project_repo, agent_loop

    class _FakeManager:
        _running_tasks: dict = {}
        def abort_employee(self, emp_id: str) -> int:
            aborted.append(emp_id)
            return 1
    monkeypatch.setattr(agent_loop, "employee_manager", _FakeManager())
    monkeypatch.setattr(project_repo, "discard_uncommitted_changes", lambda repo: discarded.append(repo))

    def fake_checkout(repo_dir, iteration, stage, branch_name=None):
        checkout_calls.append((repo_dir, iteration, stage, branch_name))
        from pathlib import Path
        import yaml
        (Path(repo_dir) / pe.STATE_FILENAME).write_text(yaml.safe_dump(restored_state))
        return branch_name or branch
    monkeypatch.setattr(project_repo, "checkout_branch_from_stage", fake_checkout)

    return {"aborted": aborted, "discarded": discarded, "checkout_calls": checkout_calls}


@pytest.mark.asyncio
async def test_revert_to_stage_cancels_active_task_and_discards_dirty_workspace(tmp_path, monkeypatch):
    """When a later stage is mid-flight, revert cancels the active task,
    discards uncommitted workspace changes from it, and proceeds with the
    checkout — the user no longer has to wait for the next gate."""
    sink = _stub_revert_environment(monkeypatch, restored_state={
        "topic": "t", "current_stage": 1, "phase": "gate",
        "stage_results": {}, "retries": 0, "end_stage": 9,
    }, branch="feat-stage2-deadbe")

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine.state["phase"] = "producer"  # mid-flight
    engine.state["active_employee_id"] = "emp-007"
    engine.state["active_node_id"] = "node-abc"
    engine._save()

    branch = await engine.revert_to_stage(stage=2, instructions="redo with X")

    assert sink["aborted"] == ["emp-007"], "active employee's task must be cancelled before checkout"
    assert sink["discarded"] == [str(tmp_path)], "uncommitted workspace changes must be discarded mid-flight"
    assert sink["checkout_calls"], "checkout must run after cancel + discard"
    assert branch == "feat-stage2-deadbe"
    assert engine.state["current_stage"] == 2
    assert engine.state["phase"] == "producer"
    assert engine.state["active_node_id"] is None
    assert engine.state["active_employee_id"] is None


@pytest.mark.asyncio
async def test_revert_to_stage_cancels_critic_phase_task(tmp_path, monkeypatch):
    """phase=critic is also mid-flight — cancel path must run."""
    sink = _stub_revert_environment(monkeypatch, restored_state={
        "topic": "t", "current_stage": 1, "phase": "gate",
        "stage_results": {}, "retries": 0, "end_stage": 9,
    })

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 4
    engine.state["phase"] = "critic"
    engine.state["active_employee_id"] = "critic-emp"
    engine._save()

    await engine.revert_to_stage(stage=2, instructions="x")

    assert sink["aborted"] == ["critic-emp"], "critic-phase revert must cancel the critic task"
    assert sink["discarded"] == [str(tmp_path)], "critic-phase revert must scrub the workspace too"


@pytest.mark.asyncio
async def test_revert_at_gate_preserves_manual_workspace_edits(tmp_path, monkeypatch):
    """At a gate, no task is running, so revert must NOT call
    discard_uncommitted_changes — that would silently wipe any manual
    edits the user made between gates. The downstream checkout's
    DirtyWorkspaceError should be the loud signal instead."""
    sink = _stub_revert_environment(monkeypatch, restored_state={
        "topic": "t", "current_stage": 1, "phase": "gate",
        "stage_results": {}, "retries": 0, "end_stage": 9,
    })

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5
    engine.state["phase"] = "gate"
    engine.state["active_employee_id"] = "stale-id"  # leftover, not actually running
    engine._save()

    await engine.revert_to_stage(stage=2, instructions="x")

    assert sink["aborted"] == [], "no cancel at a gate"
    assert sink["discarded"] == [], "no destructive workspace scrub at a gate"
    assert sink["checkout_calls"], "checkout still runs"


@pytest.mark.asyncio
async def test_revert_to_stage_validates_stage_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda *a, **k: None)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5
    engine.state["phase"] = "gate"
    engine.state["end_stage"] = 9
    engine._save()

    with pytest.raises(ValueError):
        await engine.revert_to_stage(stage=0, instructions="x")
    with pytest.raises(ValueError):
        await engine.revert_to_stage(stage=10, instructions="x")


@pytest.mark.asyncio
async def test_revert_to_stage_refuses_when_no_employee_with_skill(tmp_path, monkeypatch):
    """Pre-flight check: if there's no agent that can run the producer
    for the target stage, refuse BEFORE touching git. Otherwise the
    user ends up on a new branch with corrupt state and no in-flight
    task — non-recoverable from the UI."""
    checkout_calls = []
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda *a, **k: None)
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: None)

    from onemancompany.core import project_repo
    monkeypatch.setattr(
        project_repo, "checkout_branch_from_stage",
        lambda *a, **k: checkout_calls.append(a) or "feat-stage3-xxxxxx",
    )

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 7
    engine.state["phase"] = "gate"
    engine.state["end_stage"] = 9
    engine._save()

    with pytest.raises(pe.RevertNotAllowedError):
        await engine.revert_to_stage(stage=3, instructions="redo with X")

    assert not checkout_calls, "git checkout must not run when no agent can handle the stage"


@pytest.mark.asyncio
async def test_revert_to_stage_raises_when_checkout_loses_state_file(tmp_path, monkeypatch):
    """Critical defence: if for any reason the snapshot we checked out
    has no ``pipeline_state.yaml``, ``_load_state`` returns ``{}``. The
    OLD code silently kept the abandoned branch's state — corrupting
    the new branch. The fix raises explicitly instead."""
    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp")
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda *a, **k: None)
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda *a, **k: None)

    from onemancompany.core import project_repo
    def fake_checkout(repo_dir, iteration, stage, branch_name=None):
        # Simulate a checkout that did NOT restore pipeline_state.yaml
        # (e.g. the file was somehow gitignored, or the snapshot
        # predates the engine writing it).
        from pathlib import Path
        path = Path(repo_dir) / pe.STATE_FILENAME
        if path.exists():
            path.unlink()
        return "feat-stage3-xxxxxx"
    monkeypatch.setattr(project_repo, "checkout_branch_from_stage", fake_checkout)

    engine = pe.PipelineEngine("p1", str(tmp_path), "topic")
    engine.state["current_stage"] = 5
    engine.state["phase"] = "gate"
    engine.state["end_stage"] = 9
    engine._save()

    with pytest.raises(pe.RevertNotAllowedError, match="pipeline_state.yaml"):
        await engine.revert_to_stage(stage=3, instructions="x")


@pytest.mark.parametrize("dirname,expected_prefix", [
    ("iter_001", "iter_001"),
    ("iter_042", "iter_042"),
    ("workspace", "iter_"),       # legacy/non-standard → synthetic
    ("p7506fc954142", "iter_"),
    ("", "iter_"),
])
def test_iteration_id_normalisation(tmp_path, dirname, expected_prefix):
    """Tag namespaces must not collide across projects with the same
    legacy basename. Standard ``iter_NNN`` dirs use the literal name;
    anything else gets a hashed synthetic id derived from the full path."""
    if dirname:
        proj_dir = tmp_path / dirname
        proj_dir.mkdir()
    else:
        proj_dir = tmp_path  # tmp_path basename is opaque hash → non-standard
    engine = pe.PipelineEngine("p1", str(proj_dir), "topic")
    iid = engine._iteration_id()
    assert iid.startswith(expected_prefix)
    if expected_prefix == "iter_":
        # Hashed: not the standard form, but stable for a given path.
        assert engine._iteration_id() == iid


# ---------------------------------------------------------------------------
# End-to-end with real git subprocess — defends against mocks-only blind spot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_real_git_restores_state_and_prunes_stage_results(tmp_path, monkeypatch):
    """Smoke test: no mocks on ``project_repo``. Verifies that:
      1. ``ensure_initialized`` actually creates a git repo.
      2. ``commit_stage`` actually commits + tags after a passed stage.
      3. ``revert_to_stage`` actually checks out a feat branch rooted at
         the previous stage's tag, restoring the workspace files.
      4. The reloaded state contains only ``stage_results`` from before
         the revert point.
    """
    iter_dir = tmp_path / "iter_001"
    iter_dir.mkdir()

    monkeypatch.setattr(pe, "_find_employee_by_skill", lambda skill: "emp-x")
    monkeypatch.setattr(pe, "load_employee_configs", lambda: {})
    monkeypatch.setattr(pe.PipelineEngine, "_dispatch_to_employee", lambda *a, **k: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_stage_event", lambda *a, **k: None)
    monkeypatch.setattr(pe.PipelineEngine, "_emit_gate_event", lambda *a, **k: None)

    engine = pe.PipelineEngine("p1", str(iter_dir), "topic")
    engine.state["end_stage"] = 9
    engine._save()

    # Manually walk through two passed stages, emulating what
    # vessel.py + the producer/critic loop would do.
    (iter_dir / "stage1.md").write_text("v1 stage 1 output\n")
    engine.state["current_stage"] = 1
    engine.state["stage_results"] = {"1": "stage 1 result"}
    engine._save()
    # ensure_initialized + first commit happen on first _on_critic_pass.
    from onemancompany.core import project_repo
    project_repo.ensure_initialized(str(iter_dir), iteration="iter_001")
    engine._on_critic_pass("stage 1 result")

    (iter_dir / "stage2.md").write_text("v1 stage 2 output\n")
    engine.state["current_stage"] = 2
    engine.state["stage_results"] = {"1": "stage 1 result", "2": "stage 2 result"}
    engine._save()
    engine._on_critic_pass("stage 2 result")

    # Sanity: both files present on the main branch.
    assert (iter_dir / "stage1.md").exists()
    assert (iter_dir / "stage2.md").exists()

    # Now revert to stage 2. Expected: branch from iter_001/stage-1's
    # commit, so stage2.md disappears from the workspace.
    branch = await engine.revert_to_stage(stage=2, instructions="please rewrite stage 2 to use approach Y")

    assert branch.startswith("feat-stage2-"), branch
    assert (iter_dir / "stage1.md").exists(), "stage 1's output must survive the revert"
    assert not (iter_dir / "stage2.md").exists(), "stage 2's output must be checked-out away"

    # Reloaded state has only stage_results from BEFORE the revert.
    assert "1" in engine.state["stage_results"]
    assert "2" not in engine.state["stage_results"]
    assert engine.state["current_stage"] == 2
    assert engine.state["phase"] == "producer"
    assert engine.state["retries"] == 0
