"""Tests for shapeitup.core.state and shapeitup.core.transitions"""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from shapeitup.core.state import (
    WorkflowState, Stage, GateStatus,
    GATED_STAGES, APPROVAL_NEXT_STAGE, REWORK_TARGET, STAGE_ORDER,
)
from shapeitup.core.transitions import (
    check_transition, check_full_gate, allowed_commands_for,
    ALWAYS_ALLOWED, TransitionResult,
)


# ── Stage enum ─────────────────────────────────────────────────────────────────

class TestStageEnum(unittest.TestCase):
    def test_all_eleven_stages_exist(self):
        self.assertEqual(len(Stage), 11)

    def test_stage_order_complete(self):
        self.assertEqual(len(STAGE_ORDER), 11)

    def test_gated_stages_count(self):
        self.assertEqual(len(GATED_STAGES), 7)

    def test_case_insensitive_lookup(self):
        self.assertEqual(Stage("DISCUSS"), Stage.DISCUSS)

    def test_invalid_stage_returns_none(self):
        self.assertIsNone(Stage._missing_("nonexistent"))


# ── Routing tables ─────────────────────────────────────────────────────────────

class TestRoutingTables(unittest.TestCase):
    def test_discuss_advances_to_capability_review(self):
        self.assertEqual(APPROVAL_NEXT_STAGE[Stage.DISCUSS], Stage.CAPABILITY_REVIEW)

    def test_review_rejects_to_implementation_planning(self):
        self.assertEqual(REWORK_TARGET[Stage.REVIEW], Stage.IMPLEMENTATION_PLANNING)

    def test_all_gated_stages_have_rework_target(self):
        for stage in GATED_STAGES:
            self.assertIn(stage, REWORK_TARGET, f"{stage} missing rework target")

    def test_done_maps_to_done(self):
        self.assertEqual(APPROVAL_NEXT_STAGE[Stage.DONE], Stage.DONE)


# ── State transitions ──────────────────────────────────────────────────────────

class TestStateTransitions(unittest.TestCase):
    def _state(self, stage=Stage.DISCUSS) -> WorkflowState:
        return WorkflowState(slug="test", current_stage=stage)

    def test_apply_approve_advances_stage(self):
        s = self._state(Stage.DISCUSS)
        s.apply_approve("Looks good")
        self.assertEqual(s.current_stage, Stage.CAPABILITY_REVIEW)

    def test_approve_sets_approval_note(self):
        s = self._state(Stage.DISCUSS)
        s.apply_approve("Approved")
        self.assertEqual(s.approval_note, "Approved")

    def test_approve_clears_rejection_reason(self):
        s = self._state(Stage.CAPABILITY_REVIEW)
        s.rejection_reason = "needs work"
        s.apply_approve()
        self.assertEqual(s.rejection_reason, "")

    def test_apply_reject_sets_rejection_reason(self):
        s = self._state(Stage.CAPABILITY_REVIEW)
        s.apply_reject("Scope too broad")
        self.assertEqual(s.rejection_reason, "Scope too broad")
        self.assertEqual(s.gate_status, GateStatus.REJECTED)

    def test_review_rejection_goes_to_implementation_planning(self):
        s = self._state(Stage.REVIEW)
        s.apply_reject("Tests incomplete")
        self.assertEqual(s.current_stage, Stage.IMPLEMENTATION_PLANNING)

    def test_apply_next_on_discuss_advances(self):
        s = self._state(Stage.DISCUSS)
        s.apply_next()
        self.assertEqual(s.current_stage, Stage.CAPABILITY_REVIEW)

    def test_apply_next_on_gated_pending_raises(self):
        s = self._state(Stage.CAPABILITY_REVIEW)
        s.gate_status = GateStatus.PENDING
        with self.assertRaises(ValueError):
            s.apply_next()

    def test_apply_block_sets_blocked(self):
        s = self._state(Stage.IMPLEMENTATION)
        s.apply_block("CI failing")
        self.assertEqual(s.gate_status, GateStatus.BLOCKED)
        self.assertEqual(s.blocked_reason, "CI failing")

    def test_gated_stage_sets_pending_on_approve(self):
        s = self._state(Stage.DISCUSS)
        s.apply_approve()
        # capability-review is gated → pending
        self.assertEqual(s.gate_status, GateStatus.PENDING)

    def test_next_action_set_on_approve(self):
        s = self._state(Stage.DISCUSS)
        s.apply_approve()
        self.assertTrue(s.next_action)


# ── Serialisation ──────────────────────────────────────────────────────────────

class TestStateSerialization(unittest.TestCase):
    def _state(self) -> WorkflowState:
        s = WorkflowState(slug="my-epic", current_stage=Stage.STORY_SLICING)
        s.active_items = "Story 1"
        return s

    def test_to_dict_has_string_stage(self):
        d = self._state().to_dict()
        self.assertIsInstance(d["current_stage"], str)

    def test_roundtrip_json(self):
        s = self._state()
        restored = WorkflowState.from_dict(json.loads(s.to_json()))
        self.assertEqual(restored.current_stage, s.current_stage)
        self.assertEqual(restored.slug, s.slug)

    def test_from_markdown_parses_stage(self):
        md = "- Current stage: story-slicing\n- Human gate status: pending\n- Slug: test"
        s = WorkflowState.from_markdown(md)
        self.assertEqual(s.current_stage, Stage.STORY_SLICING)
        self.assertEqual(s.gate_status, GateStatus.PENDING)

    def test_markdown_render_contains_stage(self):
        s = self._state()
        md = s.to_markdown()
        self.assertIn("story-slicing", md)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            s = self._state()
            s.save(d)
            loaded = WorkflowState.load(d)
            self.assertEqual(loaded.current_stage, Stage.STORY_SLICING)
            self.assertEqual(loaded.slug, "my-epic")

    def test_load_prefers_json_over_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            s = WorkflowState(slug="json-source", current_stage=Stage.REVIEW)
            s.save(d)
            # Corrupt the MD to prove JSON is preferred
            (d / "state.md").write_text("- Current stage: done")
            loaded = WorkflowState.load(d)
            self.assertEqual(loaded.current_stage, Stage.REVIEW)

    def test_load_returns_blank_when_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = WorkflowState.load(Path(tmp))
            self.assertEqual(s.current_stage, Stage.DISCUSS)

    def test_atomic_write_creates_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            WorkflowState(slug="x").save(d)
            self.assertTrue((d / "state.json").exists())
            self.assertTrue((d / "state.md").exists())


# ── Transition table ───────────────────────────────────────────────────────────

class TestTransitionTable(unittest.TestCase):
    def test_always_allowed_bypass(self):
        for cmd in ALWAYS_ALLOWED:
            r = check_transition(Stage.DONE, cmd)
            self.assertTrue(r.allowed, f"{cmd} should always be allowed")

    def test_override_always_allowed(self):
        r = check_transition(Stage.DONE, "override")
        self.assertTrue(r.allowed)

    def test_approve_allowed_in_discuss(self):
        r = check_transition(Stage.DISCUSS, "approve")
        self.assertTrue(r.allowed)

    def test_approve_allowed_at_every_gated_stage(self):
        for stage in GATED_STAGES:
            r = check_transition(stage, "approve")
            self.assertTrue(r.allowed, f"approve should be allowed at {stage}")

    def test_team_run_only_in_implementation(self):
        self.assertTrue(check_transition(Stage.IMPLEMENTATION, "team-run").allowed)
        self.assertFalse(check_transition(Stage.DISCUSS, "team-run").allowed)

    def test_ci_feedback_only_in_implementation(self):
        self.assertTrue(check_transition(Stage.IMPLEMENTATION, "ci-feedback").allowed)
        self.assertFalse(check_transition(Stage.STORY_SLICING, "ci-feedback").allowed)

    def test_disallowed_returns_error_message(self):
        r = check_transition(Stage.DISCUSS, "merge-gate")
        self.assertFalse(r.allowed)
        msg = r.error_message()
        self.assertIn("merge-gate", msg)
        self.assertIn("discuss", msg)

    def test_unknown_stage_fails_open(self):
        # Create a mock stage-like object not in ALLOWED
        class FakeStage:
            value = "future-stage"
        r = check_transition(FakeStage(), "any-command")  # type: ignore
        self.assertTrue(r.allowed)

    def test_allowed_commands_for_returns_frozenset(self):
        cmds = allowed_commands_for(Stage.IMPLEMENTATION)
        self.assertIsInstance(cmds, frozenset)
        self.assertIn("team-run", cmds)
        self.assertIn("override", cmds)


# ── Full gate (transition + team) ──────────────────────────────────────────────

class TestFullGateCheck(unittest.TestCase):
    def _approved_team(self):
        from shapeitup.core.team import assemble_team_from_signals, StorySignals, Verdict
        team = assemble_team_from_signals(StorySignals())
        for role in team.blocking_roles:
            team.record_verdict(role.name, Verdict.APPROVE)
        return team

    def _pending_team(self):
        from shapeitup.core.team import assemble_team_from_signals, StorySignals
        return assemble_team_from_signals(StorySignals())

    def test_can_proceed_when_both_pass(self):
        team = self._approved_team()
        result = check_full_gate(Stage.CAPABILITY_REVIEW, "approve", team)
        self.assertTrue(result.can_proceed)

    def test_blocked_when_team_pending(self):
        team = self._pending_team()
        result = check_full_gate(Stage.CAPABILITY_REVIEW, "approve", team)
        self.assertFalse(result.can_proceed)
        self.assertFalse(result.team_gate_passed)

    def test_blocked_when_transition_disallowed(self):
        team = self._approved_team()
        result = check_full_gate(Stage.DISCUSS, "merge-gate", team)
        self.assertFalse(result.can_proceed)
        self.assertFalse(result.transition_allowed)

    def test_no_team_skips_team_gate(self):
        result = check_full_gate(Stage.DISCUSS, "approve", team=None)
        self.assertTrue(result.team_gate_passed)

    def test_error_message_combines_both(self):
        from shapeitup.core.team import assemble_team_from_signals, StorySignals, Verdict
        team = assemble_team_from_signals(StorySignals())
        team.record_verdict("product-owner",
                            Verdict.BLOCK, blocking_findings=["wrong scope"])
        result = check_full_gate(Stage.CAPABILITY_REVIEW, "approve", team)
        msg = result.error_message()
        self.assertTrue(msg)

    def test_non_advance_commands_skip_team_gate(self):
        # dag-sync at any stage should not require team verdicts
        team = self._pending_team()
        result = check_full_gate(Stage.IMPLEMENTATION, "dag-sync", team)
        self.assertTrue(result.can_proceed)


if __name__ == "__main__":
    unittest.main()
