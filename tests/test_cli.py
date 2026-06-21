"""Integration tests for shapeitup.cli — end-to-end command dispatch."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from shapeitup.cli import run


class TestCLIApproveRejectFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _approve_gate(self, reason="ok"):
        """Submit verdicts from all blocking roles then approve."""
        for role in ("product-owner", "tech-lead", "qa-engineer"):
            self._run("team-verdict", role=role, verdict="approve", reason=reason)
        self._run("approve", reason=reason)

    def test_initial_stage_is_discuss(self):
        r = self._run("actions")
        self.assertEqual(r["stage"], "discuss")

    def test_approve_advances_stage(self):
        for role in ("product-owner", "tech-lead", "qa-engineer"):
            self._run("team-verdict", role=role, verdict="approve")
        r = self._run("approve", reason="capabilities look good")
        self.assertEqual(r["stage"], "capability-review")
        self.assertTrue(r["ok"])

    def test_reject_sets_rejection(self):
        for role in ("product-owner", "tech-lead", "qa-engineer"):
            self._run("team-verdict", role=role, verdict="approve")
        self._run("approve")  # advance to capability-review
        r = self._run("reject", reason="too broad")
        self.assertEqual(r["gate_status"], "rejected")

    def test_disallowed_command_returns_error(self):
        r = run(slug="t", root=self.root, command="merge-gate",
                output_format="json")
        self.assertFalse(r["ok"])
        self.assertIn("merge-gate", r["error"])

    def test_override_bypasses_gate(self):
        r = self._run("override", reason="emergency")
        self.assertTrue(r["ok"])

    def test_state_persists_across_calls(self):
        for role in ("product-owner", "tech-lead", "qa-engineer"):
            self._run("team-verdict", role=role, verdict="approve")
        self._run("approve")
        r = self._run("actions")
        self.assertEqual(r["stage"], "capability-review")

    def test_result_has_team_info(self):
        r = self._run("actions")
        self.assertIn("team", r)
        self.assertIn("product-owner", r["team"]["active_roles"])

    def test_next_action_populated(self):
        r = self._run("actions")
        self.assertTrue(r["next_action"])

    def test_reconcile_stays_at_stage(self):
        r = self._run("reconcile", reason="code ahead of design")
        self.assertEqual(r["stage"], "discuss")

    def test_defer_accumulates_items(self):
        # defer is only available at implementation-planning — advance through all gated stages
        for _ in range(6):  # 6 gated stages: cap-review, epic, story-slice, story-enrich, spec, then impl-planning via next
            for role in ("product-owner", "tech-lead", "qa-engineer"):
                self._run("team-verdict", role=role, verdict="approve")
            self._run("approve")
        # Now at implementation-planning (non-gated)
        r = self._run("defer", reason="Story 2")
        self.assertTrue(r["ok"])

    def test_memory_record_writes_file(self):
        self._run("memory-record", reason="Use pytest fixtures for temp dirs")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "records" / "memory.jsonl").exists())

    def test_debt_record_writes_file(self):
        self._run("debt-record", reason="Integration tests deferred")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "records" / "debt.jsonl").exists())


class TestCLIAnalysisCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def _approve_gate(self, reason="ok"):
        for role in ("product-owner", "tech-lead", "qa-engineer"):
            self._run("team-verdict", role=role, verdict="approve", reason=reason)
        self._run("approve", reason=reason)

    def _advance_to_implementation(self):
        """Advance through all gated stages to reach implementation.
        6 approve-gate cycles: discuss→cap-review→epic→story-slice→story-enrich→spec→impl-planning
        Then `next` from impl-planning→implementation (next, not approve).
        """
        for _ in range(6):
            self._approve_gate()
        self._run("next")  # implementation-planning → implementation

    def test_ci_feedback_classifies_failure(self):
        self._advance_to_implementation()
        r = self._run("ci-feedback", reason="3 tests failed assertionerror expected 5 got 3")
        self.assertTrue(r["ok"])
        self.assertEqual(r["ml_outputs"]["failure_class"], "test_failure")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "ci-feedback.json").exists())

    def test_ci_feedback_high_severity_blocks(self):
        self._advance_to_implementation()
        r = self._run("ci-feedback", reason="build failed SyntaxError unexpected token")
        self.assertEqual(r["gate_status"], "blocked")

    def test_ci_feedback_retryable_does_not_block(self):
        self._advance_to_implementation()
        r = self._run("ci-feedback", reason="flaky test detected retry attempt 2")
        self.assertNotEqual(r["gate_status"], "blocked")

    def test_execution_path_classifies_simple(self):
        self._advance_to_implementation()
        r = self._run("execution-path",
                      reason="Add a helper to format currency values. "
                             "AC: given a float, returns formatted string.")
        self.assertTrue(r["ok"])
        self.assertIn(r["ml_outputs"]["path_type"], ("simple", "flagged"))

    def test_execution_path_writes_artifacts(self):
        self._advance_to_implementation()
        self._run("execution-path", reason="simple story text here")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "execution-path.json").exists())
        self.assertTrue((wf / "execution-path.md").exists())

    def test_dag_sync_no_stories_warns(self):
        r = self._run("dag-sync")
        self.assertFalse(r["ok"] and not r["warnings"])

    def test_dag_sync_with_stories(self):
        wf = self.root / ".workflow" / "test-epic"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "stories.md").write_text(
            "## Story 1\nDo the first thing.\n\n"
            "## Story 2\nDepends on: Story 1\nDo the second thing.\n",
            encoding="utf-8",
        )
        r = self._run("dag-sync")
        self.assertTrue(r["ok"])
        dag_data = json.loads((wf / "dag.json").read_text())
        self.assertIn("Story 1", dag_data["nodes"])
        self.assertIn("Story 2", dag_data["nodes"])
        self.assertEqual(dag_data["edges"]["Story 2"], ["Story 1"])

    def test_drift_check_no_design_warns(self):
        r = self._run("drift-check")
        self.assertTrue(r["warnings"])

    def test_actions_writes_menu(self):
        self._run("actions")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "action-menu.md").exists())


class TestCLITeamCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def _run(self, command, **kwargs):
        return run(slug="test-epic", root=self.root, command=command, **kwargs)

    def test_team_verdict_approve(self):
        r = self._run("team-verdict", role="product-owner", verdict="approve",
                      reason="Scope is correct")
        self.assertTrue(r["ok"])

    def test_team_verdict_block_blocks_gate(self):
        r = self._run("team-verdict", role="product-owner", verdict="block",
                      findings="out of scope", reason="wrong feature")
        self.assertTrue(r["ok"])
        self.assertEqual(r["gate_status"], "blocked")

    def test_challenge_writes_review_log(self):
        self._run("challenge", role="qa-engineer",
                  reason="acceptance criteria are not testable")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "review-log.md").exists())

    def test_accounting_record_appends(self):
        self._run("accounting-record", reason="role: Implementer; tokens: 1200; cost: 0.02")
        wf = self.root / ".workflow" / "test-epic"
        self.assertTrue((wf / "records" / "invocations.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
