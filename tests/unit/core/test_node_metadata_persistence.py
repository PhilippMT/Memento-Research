"""Regression: ``TaskNode.metadata`` must round-trip through YAML save +
load. Otherwise the ``pipeline_managed`` tag set by
``pipeline_engine._dispatch_to_employee`` evaporates on every tree
reload, which breaks the ``tree.has_pipeline_managed_nodes()`` guard
in ``vessel.py``'s project-completion heuristic — Stage 1's producer
gets misidentified as the EA orchestrator, the tree is mistakenly
declared "PROJECT COMPLETE", and the CEO confirm interaction fires
while stages 2-9 are still queued (production case from issue #82).

The reported task_tree.yaml had **0 nodes** carrying ``pipeline_managed``
metadata, even though every stage dispatch path sets it. Root cause:
``metadata`` was never a dataclass field on ``TaskNode``, so ``to_dict``
didn't write it and ``from_dict`` couldn't read it back. The attribute
existed in memory until the next save and vanished thereafter."""
from __future__ import annotations

import yaml

import pytest


class TestMetadataIsADataclassField:
    def test_default_metadata_is_empty_dict(self):
        """Default factory must produce an empty dict so callers can
        ``node.metadata['x'] = y`` without checking for None."""
        from onemancompany.core.task_tree import TaskNode
        n = TaskNode(id="n1")
        assert n.metadata == {}
        # Independent default per instance (no shared mutable default).
        m = TaskNode(id="n2")
        m.metadata["x"] = 1
        assert "x" not in n.metadata

    def test_metadata_listed_in_dataclass_fields(self):
        """``from_dict`` filters by ``__dataclass_fields__``. If
        metadata isn't a field, load drops it silently."""
        from onemancompany.core.task_tree import TaskNode
        assert "metadata" in TaskNode.__dataclass_fields__


class TestMetadataRoundTrip:
    def test_to_dict_emits_metadata(self):
        from onemancompany.core.task_tree import TaskNode
        n = TaskNode(id="n1")
        n.metadata = {"pipeline_managed": True, "stage_id": 2}
        d = n.to_dict()
        assert d.get("metadata") == {"pipeline_managed": True, "stage_id": 2}

    def test_to_dict_omits_or_emits_empty_for_default(self):
        """Default-empty metadata can either be omitted or emit ``{}``;
        either is fine. The contract is "no garbage if unused"."""
        from onemancompany.core.task_tree import TaskNode
        n = TaskNode(id="n1")
        d = n.to_dict()
        # Whatever the choice, ``from_dict`` must still produce ``{}``.
        round_tripped = TaskNode.from_dict(d)
        assert round_tripped.metadata == {}

    def test_from_dict_restores_metadata(self):
        from onemancompany.core.task_tree import TaskNode
        n = TaskNode(id="n1")
        n.metadata = {"pipeline_managed": True}
        loaded = TaskNode.from_dict(n.to_dict())
        assert loaded.metadata == {"pipeline_managed": True}


class TestPipelineManagedSurvivesSaveLoadCycle:
    """The end-to-end regression: dispatch tags a node, the tree is saved
    to YAML, the backend restarts (we simulate via a fresh load), and the
    ``has_pipeline_managed_nodes`` guard must STILL return True. This is
    the property the production bug violated."""

    def test_has_pipeline_managed_nodes_survives_yaml_round_trip(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree, TaskNode

        tree = TaskTree(project_id="proj-1")
        ceo = tree.create_root(employee_id="00001", description="CEO root")
        # Mirror what pipeline_engine._dispatch_to_employee does today:
        # add a node and tag it as pipeline-managed.
        n = tree.add_child(parent_id=ceo.id, employee_id="00006",
                           description="Stage 1 producer",
                           acceptance_criteria=["done"])
        n.metadata = {"pipeline_managed": True}

        assert tree.has_pipeline_managed_nodes() is True, (
            "sanity: tag must work in-memory before persistence"
        )

        # Persist exactly the way save_tree_async would.
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        # Fresh load — what happens after any restart.
        loaded = TaskTree.load(path, project_id="proj-1")
        assert loaded.has_pipeline_managed_nodes() is True, (
            "tag must survive YAML round trip — otherwise vessel.py's "
            "is_project_complete guard fails open after restart and "
            "Stage 1's producer is misread as the EA orchestrator, "
            "tripping a premature PROJECT COMPLETE while stages 2-9 "
            "are still queued (issue #82 production case)"
        )

    def test_metadata_content_preserved_for_non_pipeline_flags_too(self, tmp_path):
        """The fix isn't about ``pipeline_managed`` specifically — any
        metadata callers attach must survive. Lock the contract so a
        future caller's tag isn't accidentally dropped."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj-1")
        ceo = tree.create_root(employee_id="00001", description="CEO root")
        n = tree.add_child(parent_id=ceo.id, employee_id="00006",
                           description="Stage 1 producer",
                           acceptance_criteria=["done"])
        n.metadata = {
            "pipeline_managed": True,
            "stage_id": 1,
            "skill": "topic_refiner",
        }

        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        loaded = TaskTree.load(path, project_id="proj-1")
        nodes = list(loaded._nodes.values())
        # Find the producer node we just added.
        producer = next(nn for nn in nodes if nn.employee_id == "00006")
        assert producer.metadata == {
            "pipeline_managed": True,
            "stage_id": 1,
            "skill": "topic_refiner",
        }
