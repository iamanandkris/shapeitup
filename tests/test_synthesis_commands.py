"""
Tests for synthesis_commands.py and impl_commands.py.

Synthesis stubs set needs_llm=True — the LLM generates the actual content.
Impl stubs handle coordination and gate checks mechanically.
"""
import json
import tempfile
import unittest
from pathlib import Path

from shapeitup.cli import run


class TestSynthesisCommandsNeedsLLM(unittest.TestCase):
    """All synthesis commands must return needs_llm=True."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto(self, stage: str):
        """Jump to a stage using override (bypasses gate — for test setup only)."""
        self._run("override", reason=f"test setup: jump to {stage}")
        # Override doesn't change stage directly; use internal state write
        from shapeitup.core.state import WorkflowState, Stage
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage(stage)
        state.save(wf)

    def test_capability_synth_needs_llm(self):
        r = self._run("capability-synth", reason="Build a payment gateway")
        self.assertTrue(r["needs_llm"])
        self.assertIn("capabilities.md", r["llm_task"])

    def test_design_synth_needs_llm(self):
        self._goto("epic-shaping")
        r = self._run("design-synth", reason="Payment gateway design")
        self.assertTrue(r["needs_llm"])
        self.assertIn("design-seed.md", r["llm_task"])

    def test_story_synth_needs_llm(self):
        self._goto("story-slicing")
        r = self._run("story-synth", reason="Slice payment epic")
        self.assertTrue(r["needs_llm"])
        self.assertIn("stories.md", r["llm_task"])

    def test_story_enrichment_synth_needs_llm(self):
        self._goto("story-enrichment")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "stories.md").write_text("## Story 1\nDo the thing.\n")
        r = self._run("story-enrichment-synth")
        self.assertTrue(r["needs_llm"])
        self.assertIn("stories.md", r["llm_task"])

    def test_openspec_synth_needs_llm(self):
        self._goto("spec-authoring")
        r = self._run("openspec-synth", reason="Define API contracts")
        self.assertTrue(r["needs_llm"])
        self.assertIn("openspec.md", r["llm_task"])

    def test_openspec_sync_no_spec_warns(self):
        self._goto("spec-authoring")
        r = self._run("openspec-sync")
        self.assertFalse(r["needs_llm"])
        self.assertIn("openspec-synth", r["message"])

    def test_openspec_sync_with_spec_needs_llm(self):
        self._goto("spec-authoring")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "openspec.md").write_text("# Spec\n")
        r = self._run("openspec-sync")
        self.assertTrue(r["needs_llm"])

    def test_implementation_plan_synth_needs_llm(self):
        self._goto("spec-authoring")
        r = self._run("implementation-plan-synth", reason="Plan the build")
        self.assertTrue(r["needs_llm"])
        self.assertIn("implementation-plan.md", r["llm_task"])

    def test_feedback_synth_needs_llm(self):
        self._goto("capability-review")
        r = self._run("feedback-synth", reason="Synthesise review notes")
        self.assertTrue(r["needs_llm"])
        self.assertIn("feedback-synthesis.md", r["llm_task"])

    def test_issue_advisor_needs_llm(self):
        self._goto("capability-review")
        r = self._run("issue-advisor", reason="Build fails on auth module")
        self.assertTrue(r["needs_llm"])
        self.assertIn("Build fails", r["llm_task"])

    def test_issue_advisor_no_reason_warns(self):
        self._goto("capability-review")
        r = self._run("issue-advisor")
        self.assertFalse(r.get("needs_llm", False))
        self.assertTrue(r["warnings"])

    def test_replan_needs_llm(self):
        self._goto("implementation-planning")
        r = self._run("replan", reason="Scope reduced by half")
        self.assertTrue(r["needs_llm"])
        self.assertIn("implementation-plan.md", r["llm_task"])

    def test_verify_fix_needs_llm(self):
        self._goto("implementation")
        r = self._run("verify-fix", reason="Changed auth middleware to validate JWT expiry")
        self.assertTrue(r["needs_llm"])
        self.assertIn("verify-fix", r["message"])

    def test_verify_fix_no_reason_warns(self):
        self._goto("implementation")
        r = self._run("verify-fix")
        self.assertFalse(r.get("needs_llm", False))
        self.assertTrue(r["warnings"])

    def test_staff_needs_llm(self):
        self._goto("epic-shaping")
        r = self._run("staff", reason="Assign stories for sprint 1")
        self.assertTrue(r["needs_llm"])
        self.assertIn("staffing", r["llm_task"])

    def test_assign_records_file(self):
        self._goto("story-slicing")
        r = self._run("assign", role="tech-lead", reason="Story 1: Setup auth module")
        self.assertTrue(r["ok"])
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "assignments.jsonl").exists())
        entry = json.loads((wf / "assignments.jsonl").read_text().strip())
        self.assertEqual(entry["role"], "tech-lead")
        self.assertIn("Story 1", entry["story"])

    def test_assign_no_reason_warns(self):
        self._goto("story-slicing")
        r = self._run("assign")
        self.assertTrue(r["warnings"])


class TestImplCommands(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _goto(self, stage: str):
        from shapeitup.core.state import WorkflowState, Stage
        self._run("override", reason=f"test setup: jump to {stage}")
        wf = self.root / ".workflow" / "test-epic"
        state = WorkflowState.load(wf)
        state.current_stage = Stage(stage)
        state.save(wf)

    def test_team_run_needs_llm(self):
        self._goto("implementation")
        r = self._run("team-run", reason="Sprint 1 kick-off")
        self.assertTrue(r["needs_llm"])
        self.assertIn("team run", r["llm_task"])

    def test_team_run_level_valid(self):
        self._goto("implementation")
        r = self._run("team-run-level", reason="story")
        self.assertTrue(r["ok"])
        wf = self.root / ".workflow" / "test-epic"
        self.assertEqual((wf / "run-level.txt").read_text(), "story")

    def test_team_run_level_invalid(self):
        self._goto("implementation")
        r = self._run("team-run-level", reason="sprint")
        self.assertFalse(r["ok"])
        self.assertTrue(r["warnings"])

    def test_team_sync_no_team(self):
        self._goto("implementation")
        # No execution-path run yet — team has blank signals
        r = self._run("team-sync")
        # Should still work with default blank-signal team
        self.assertTrue(r["ok"])
        self.assertIn("team-sync", r["message"])

    def test_team_sync_writes_file(self):
        self._goto("implementation")
        self._run("team-sync")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "team-sync.json").exists())
        data = json.loads((wf / "team-sync.json").read_text())
        self.assertIn("roles", data)
        self.assertIn("gate_can_advance", data)

    def test_merge_gate_no_data_requests_advisory(self):
        self._goto("implementation")
        r = self._run("merge-gate")
        self.assertTrue(r["needs_llm"])

    def test_merge_gate_high_severity_blocks(self):
        self._goto("implementation")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        ci = {"severity": "high", "is_retryable": False, "failure_class": "compilation_error"}
        (wf / "ci-feedback.json").write_text(json.dumps(ci))
        r = self._run("merge-gate")
        self.assertFalse(r["ok"])
        self.assertTrue(r["warnings"])

    def test_merge_gate_drift_blocks(self):
        self._goto("implementation")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        drift = {"needs_reconciliation": True, "drift_type": "code_ahead", "score": 0.28}
        (wf / "drift-check.json").write_text(json.dumps(drift))
        r = self._run("merge-gate")
        self.assertFalse(r["ok"])

    def test_merge_apply_records_entry(self):
        self._goto("implementation")
        r = self._run("merge-apply", reason="Merge PR #42 — Story 3 complete")
        self.assertTrue(r["ok"])
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "merge-log.jsonl").exists())
        entry = json.loads((wf / "merge-log.jsonl").read_text().strip())
        self.assertIn("Story 3", entry["note"])

    def test_integration_gate_no_dag_requests_advisory(self):
        self._goto("implementation")
        r = self._run("integration-gate")
        self.assertTrue(r["needs_llm"])
        self.assertIn("dag-sync", r["llm_task"])

    def test_integration_gate_with_clean_dag_passes(self):
        self._goto("implementation")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        dag = {"nodes": ["Story 1", "Story 2"], "edges": {"Story 2": ["Story 1"]}, "errors": []}
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("integration-gate")
        self.assertTrue(r["ok"])
        self.assertIn("PASSED", r["message"])

    def test_integration_gate_with_dag_errors_blocks(self):
        self._goto("implementation")
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        dag = {
            "nodes": ["Story 1", "Story 2"], "edges": {},
            "errors": ["'Story 2' depends on unknown story 'Story 3'"]
        }
        (wf / "dag.json").write_text(json.dumps(dag))
        r = self._run("integration-gate")
        self.assertFalse(r["ok"])
        self.assertTrue(r["warnings"])


if __name__ == "__main__":
    unittest.main()
