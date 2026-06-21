"""
Tests for agent_commands.py, stage_planner.py, and impl_scheduler.py.
"""
import json
import tempfile
import unittest
from pathlib import Path

from shapeitup.cli import run
from shapeitup.core.state import WorkflowState, Stage


class TestStagePlan(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto(self, stage: str):
        self._run("override", reason="test setup")
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage(stage)
        state.save(wf)

    def test_stage_plan_returns_json(self):
        r = self._run("stage-plan")
        self.assertTrue(r["ok"])
        plan = r["ml_outputs"]
        self.assertIn("stage", plan)
        self.assertIn("phases", plan)
        self.assertIsInstance(plan["phases"], list)

    def test_stage_plan_has_generation_phase(self):
        r = self._run("stage-plan")
        plan = r["ml_outputs"]
        # Phase 1 should be generation
        phase1 = plan["phases"][0]
        self.assertFalse(phase1["parallel"])
        self.assertEqual(len(phase1["tasks"]), 1)
        self.assertEqual(phase1["tasks"][0]["role"], "system")

    def test_stage_plan_has_parallel_review_phase(self):
        r = self._run("stage-plan")
        plan = r["ml_outputs"]
        # Phase 2 should be parallel review
        phase2 = plan["phases"][1]
        self.assertTrue(phase2["parallel"])
        roles = [t["role"] for t in phase2["tasks"]]
        self.assertIn("product-owner", roles)
        self.assertIn("tech-lead", roles)
        self.assertIn("qa-engineer", roles)

    def test_stage_plan_writes_file(self):
        self._run("stage-plan")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "stage-plan.json").exists())

    def test_stage_plan_at_different_stages(self):
        for stage in ("capability-review", "epic-shaping", "story-slicing"):
            self._goto(stage)
            r = self._run("stage-plan")
            self.assertTrue(r["ok"])
            self.assertEqual(r["ml_outputs"]["stage"], stage)

    def test_stage_plan_implementation_is_special(self):
        self._goto("implementation")
        r = self._run("stage-plan")
        self.assertTrue(r["ok"])
        plan = r["ml_outputs"]
        self.assertEqual(plan["stage"], "implementation")
        # Implementation plan references impl-schedule
        all_cmds = [t["command"] for p in plan["phases"] for t in p["tasks"]]
        self.assertIn("impl-schedule", all_cmds)


class TestImplSchedule(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto_impl(self):
        self._run("override", reason="test setup")
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage.IMPLEMENTATION
        state.save(wf)
        return wf

    def test_impl_schedule_no_dag_fails(self):
        self._goto_impl()
        r = self._run("impl-schedule")
        self.assertFalse(r["ok"])
        self.assertTrue(r["warnings"])

    def test_impl_schedule_simple_dag(self):
        wf = self._goto_impl()
        dag = {
            "nodes": ["Story 1", "Story 2", "Story 3"],
            "edges": {"Story 1": [], "Story 2": [], "Story 3": ["Story 1"]},
            "errors": [],
        }
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("impl-schedule")
        self.assertTrue(r["ok"])
        schedule = r["ml_outputs"]
        self.assertEqual(schedule["total_stories"], 3)
        groups = schedule["groups"]
        self.assertEqual(len(groups), 2)
        # Group 1: Story 1 and Story 2 (no deps, parallel)
        group1_stories = [s["story"] for s in groups[0]["stories"]]
        self.assertIn("Story 1", group1_stories)
        self.assertIn("Story 2", group1_stories)
        # Group 2: Story 3 (depends on Story 1)
        group2_stories = [s["story"] for s in groups[1]["stories"]]
        self.assertIn("Story 3", group2_stories)

    def test_impl_schedule_tdd_phases_per_story(self):
        wf = self._goto_impl()
        dag = {
            "nodes": ["Story 1"],
            "edges": {"Story 1": []},
            "errors": [],
        }
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("impl-schedule")
        story = r["ml_outputs"]["groups"][0]["stories"][0]
        phases = story["phases"]
        labels = [p["label"] for p in phases]
        self.assertTrue(any("failing tests" in l.lower() for l in labels))
        self.assertTrue(any("pair" in l.lower() for l in labels))
        self.assertTrue(any("validation" in l.lower() for l in labels))

    def test_impl_schedule_qa_test_spec_is_phase1(self):
        wf = self._goto_impl()
        dag = {"nodes": ["Story 1"], "edges": {"Story 1": []}, "errors": []}
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("impl-schedule")
        phases = r["ml_outputs"]["groups"][0]["stories"][0]["phases"]
        phase1 = phases[0]
        self.assertFalse(phase1["parallel"])
        self.assertEqual(phase1["tasks"][0]["command"], "qa-test-spec")

    def test_impl_schedule_validation_is_parallel(self):
        wf = self._goto_impl()
        dag = {"nodes": ["Story 1"], "edges": {"Story 1": []}, "errors": []}
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("impl-schedule")
        phases = r["ml_outputs"]["groups"][0]["stories"][0]["phases"]
        phase3 = phases[2]
        self.assertTrue(phase3["parallel"])
        cmds = [t["command"] for t in phase3["tasks"]]
        self.assertIn("qa-validate", cmds)
        self.assertIn("tl-impl-review", cmds)

    def test_impl_schedule_writes_file(self):
        wf = self._goto_impl()
        dag = {"nodes": ["Story 1"], "edges": {"Story 1": []}, "errors": []}
        (wf / "dag.json").write_text(json.dumps(dag))
        self._run("impl-schedule")
        self.assertTrue((wf / "impl-schedule.json").exists())


class TestAgentReviewCommands(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto(self, stage: str):
        self._run("override", reason="test setup")
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage(stage)
        state.save(wf)

    def test_po_review_needs_llm(self):
        self._goto("capability-review")
        r = self._run("po-review")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Product Owner", r["llm_task"])

    def test_tl_review_needs_llm(self):
        self._goto("capability-review")
        r = self._run("tl-review")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Tech Lead", r["llm_task"])

    def test_qa_review_needs_llm(self):
        self._goto("capability-review")
        r = self._run("qa-review")
        self.assertTrue(r["needs_llm"])
        self.assertIn("QA Engineer", r["llm_task"])

    def test_security_scan_needs_llm(self):
        self._goto("capability-review")
        r = self._run("security-scan")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Security Reviewer", r["llm_task"])

    def test_po_review_includes_stage_in_task(self):
        self._goto("epic-shaping")
        r = self._run("po-review")
        self.assertIn("epic-shaping", r["llm_task"])

    def test_po_review_includes_artifact_path(self):
        self._goto("capability-review")
        r = self._run("po-review")
        self.assertIn("reviews/product-owner-review-capability-review.md", r["llm_task"])

    def test_tl_review_artifact_path_contains_stage(self):
        self._goto("story-slicing")
        r = self._run("tl-review")
        self.assertIn("story-slicing", r["llm_task"])

    def test_reviews_available_at_all_gated_stages(self):
        for stage in ("capability-review", "epic-shaping", "story-slicing",
                      "story-enrichment", "spec-authoring"):
            self._goto(stage)
            for cmd in ("po-review", "tl-review", "qa-review"):
                r = self._run(cmd)
                self.assertTrue(r["ok"], f"{cmd} failed at {stage}: {r.get('message')}")
                self.assertTrue(r["needs_llm"], f"{cmd} should need LLM at {stage}")


class TestTDDCommands(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto_impl(self):
        self._run("override", reason="test setup")
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage.IMPLEMENTATION
        state.save(wf)
        return wf

    def test_qa_test_spec_needs_llm(self):
        self._goto_impl()
        r = self._run("qa-test-spec", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("QA Engineer", r["llm_task"])
        self.assertIn("failing test", r["llm_task"].lower())

    def test_qa_test_spec_no_story_fails(self):
        self._goto_impl()
        r = self._run("qa-test-spec")
        self.assertFalse(r["ok"])

    def test_pair_propose_needs_llm(self):
        self._goto_impl()
        r = self._run("pair-propose", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Proposer", r["llm_task"])

    def test_pair_challenge_needs_llm(self):
        self._goto_impl()
        r = self._run("pair-challenge", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Challenger", r["llm_task"])

    def test_pair_implement_needs_llm(self):
        self._goto_impl()
        r = self._run("pair-implement", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("consensus", r["llm_task"].lower())

    def test_tl_impl_review_needs_llm(self):
        self._goto_impl()
        r = self._run("tl-impl-review", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Tech Lead", r["llm_task"])

    def test_qa_validate_needs_llm(self):
        self._goto_impl()
        r = self._run("qa-validate", reason="Story 1: Add payment webhook")
        self.assertTrue(r["needs_llm"])
        self.assertIn("QA Engineer", r["llm_task"])

    def test_tdd_commands_require_story(self):
        self._goto_impl()
        for cmd in ("qa-test-spec", "pair-propose", "pair-challenge",
                    "pair-implement", "tl-impl-review", "qa-validate"):
            r = self._run(cmd)
            self.assertFalse(r["ok"], f"{cmd} should fail without story")

    def test_pair_propose_reads_test_spec_if_exists(self):
        wf = self._goto_impl()
        (wf / "reviews").mkdir(parents=True, exist_ok=True)
        (wf / "reviews" / "qa-test-spec-story-1-add-payment-webhook.md").write_text(
            "## Tests\n- test_webhook_signature_valid\n"
        )
        r = self._run("pair-propose", reason="Story 1: Add payment webhook")
        self.assertIn("test_webhook_signature_valid", r["llm_task"])


class TestRoleAgentTasks(unittest.TestCase):

    def test_product_owner_has_agent_task(self):
        from shapeitup.core.team import PRODUCT_OWNER
        self.assertTrue(len(PRODUCT_OWNER.agent_tasks) > 0)
        task = PRODUCT_OWNER.agent_tasks[0]
        self.assertEqual(task.command, "po-review")

    def test_tech_lead_has_impl_review_task(self):
        from shapeitup.core.team import TECH_LEAD
        impl_tasks = TECH_LEAD.tasks_for_stage("implementation")
        cmds = [t.command for t in impl_tasks]
        self.assertIn("tl-impl-review", cmds)

    def test_qa_has_test_spec_task_at_impl(self):
        from shapeitup.core.team import QA_ENGINEER
        impl_tasks = QA_ENGINEER.tasks_for_stage("implementation")
        cmds = [t.command for t in impl_tasks]
        self.assertIn("qa-test-spec", cmds)
        self.assertIn("qa-validate", cmds)

    def test_implementer_has_pair_tasks(self):
        from shapeitup.core.team import IMPLEMENTER
        tasks = IMPLEMENTER.tasks_for_stage("implementation")
        cmds = [t.command for t in tasks]
        self.assertIn("pair-propose", cmds)
        self.assertIn("pair-challenge", cmds)

    def test_security_reviewer_has_scan_task(self):
        from shapeitup.core.team import SECURITY_REVIEWER
        tasks = SECURITY_REVIEWER.tasks_for_stage("discuss")
        self.assertEqual(tasks[0].command, "security-scan")

    def test_review_artifact_path_format(self):
        from shapeitup.core.team import PRODUCT_OWNER
        path = PRODUCT_OWNER.review_artifact_path("discuss")
        self.assertEqual(path, "reviews/product-owner-review-discuss.md")


if __name__ == "__main__":
    unittest.main()
