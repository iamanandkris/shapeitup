"""
Tests for shapeitup.core.team

Verifies that team assembly, activation, verdict recording, and gate
enforcement are all driven by ML signals — not by markdown config.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from shapeitup.core.team import (
    ActiveTeam, Role, RoleVerdict, StorySignals, Verdict,
    ActivationCondition, DEFAULT_ROLES,
    PRODUCT_OWNER, TECH_LEAD, IMPLEMENTER, QA_ENGINEER, SECURITY_REVIEWER,
    assemble_team, assemble_team_from_signals,
)


# ── Role definitions ───────────────────────────────────────────────────────────

class TestRoleDefinitions(unittest.TestCase):
    def test_product_owner_always_active(self):
        self.assertTrue(PRODUCT_OWNER.always_active)

    def test_tech_lead_always_active(self):
        self.assertTrue(TECH_LEAD.always_active)

    def test_implementer_always_active(self):
        self.assertTrue(IMPLEMENTER.always_active)

    def test_qa_always_active(self):
        self.assertTrue(QA_ENGINEER.always_active)

    def test_security_reviewer_not_always_active(self):
        self.assertFalse(SECURITY_REVIEWER.always_active)

    def test_implementer_does_not_block_gate(self):
        self.assertFalse(IMPLEMENTER.blocks_gate)

    def test_product_owner_blocks_gate(self):
        self.assertTrue(PRODUCT_OWNER.blocks_gate)

    def test_tech_lead_blocks_gate(self):
        self.assertTrue(TECH_LEAD.blocks_gate)

    def test_qa_blocks_gate(self):
        self.assertTrue(QA_ENGINEER.blocks_gate)

    def test_security_reviewer_blocks_gate(self):
        self.assertTrue(SECURITY_REVIEWER.blocks_gate)

    def test_all_roles_have_bias(self):
        for role in DEFAULT_ROLES:
            self.assertTrue(role.bias, f"{role.name} has no bias")

    def test_all_roles_have_review_artifacts(self):
        for role in DEFAULT_ROLES:
            self.assertTrue(role.review_artifacts, f"{role.name} has no review_artifacts")


# ── Security reviewer activation ───────────────────────────────────────────────

class TestSecurityReviewerActivation(unittest.TestCase):
    def _signals(self, **kwargs) -> StorySignals:
        return StorySignals(**kwargs)

    def test_activates_on_security_signal(self):
        s = self._signals(security_signal=True)
        self.assertTrue(SECURITY_REVIEWER.is_active_for(s))

    def test_activates_on_flagged(self):
        s = self._signals(flagged=True)
        self.assertTrue(SECURITY_REVIEWER.is_active_for(s))

    def test_activates_on_high_interface(self):
        s = self._signals(interface_signals=3)
        self.assertTrue(SECURITY_REVIEWER.is_active_for(s))

    def test_not_active_for_simple_story(self):
        s = self._signals(flagged=False, security_signal=False,
                          multi_service_signal=False, interface_signals=1)
        self.assertFalse(SECURITY_REVIEWER.is_active_for(s))

    def test_interface_threshold_exact(self):
        self.assertFalse(SECURITY_REVIEWER.is_active_for(self._signals(interface_signals=2)))
        self.assertTrue(SECURITY_REVIEWER.is_active_for(self._signals(interface_signals=3)))


# ── Team assembly ──────────────────────────────────────────────────────────────

class TestTeamAssembly(unittest.TestCase):
    def _simple_signals(self) -> StorySignals:
        return StorySignals(flagged=False, security_signal=False,
                            interface_signals=1)

    def _flagged_signals(self) -> StorySignals:
        return StorySignals(flagged=True, security_signal=True,
                            interface_signals=4)

    def test_simple_story_has_four_roles(self):
        team = assemble_team_from_signals(self._simple_signals())
        # PO + TechLead + Implementer + QA (no Security)
        self.assertEqual(len(team.roles), 4)

    def test_simple_story_excludes_security_reviewer(self):
        team = assemble_team_from_signals(self._simple_signals())
        self.assertNotIn("security-reviewer", team.active_role_names)

    def test_flagged_story_has_five_roles(self):
        team = assemble_team_from_signals(self._flagged_signals())
        self.assertEqual(len(team.roles), 5)

    def test_flagged_story_includes_security_reviewer(self):
        team = assemble_team_from_signals(self._flagged_signals())
        self.assertIn("security-reviewer", team.active_role_names)

    def test_product_owner_always_present(self):
        for signals in [self._simple_signals(), self._flagged_signals()]:
            team = assemble_team_from_signals(signals)
            self.assertIn("product-owner", team.active_role_names)

    def test_all_verdicts_initialised_pending(self):
        team = assemble_team_from_signals(self._simple_signals())
        for rv in team.verdicts.values():
            self.assertEqual(rv.verdict, Verdict.PENDING)

    def test_assemble_team_from_text(self):
        simple = "Add a helper to format currency values"
        team = assemble_team(simple)
        self.assertIn("product-owner", team.active_role_names)
        self.assertIn("tech-lead", team.active_role_names)

    def test_security_story_from_text(self):
        secure = (
            "Implement JWT refresh token rotation with Redis-backed token store. "
            "Auth endpoint: POST /auth/refresh. Depends on: Story 1, Story 2."
        )
        team = assemble_team(secure)
        self.assertIn("security-reviewer", team.active_role_names)


# ── Verdict recording ──────────────────────────────────────────────────────────

class TestVerdictRecording(unittest.TestCase):
    def _team(self) -> ActiveTeam:
        return assemble_team_from_signals(StorySignals())

    def test_record_approve(self):
        team = self._team()
        team.record_verdict("product-owner", Verdict.APPROVE, summary="Looks good")
        self.assertEqual(team.verdicts["product-owner"].verdict, Verdict.APPROVE)

    def test_record_block_with_findings(self):
        team = self._team()
        team.record_verdict("tech-lead", Verdict.BLOCK,
                            blocking_findings=["circular dependency detected"])
        rv = team.verdicts["tech-lead"]
        self.assertTrue(rv.is_blocking)
        self.assertIn("circular dependency detected", rv.blocking_findings)

    def test_record_verdict_inactive_role_raises(self):
        # Security reviewer not active for simple story
        team = assemble_team_from_signals(
            StorySignals(flagged=False, security_signal=False, interface_signals=1)
        )
        with self.assertRaises(ValueError):
            team.record_verdict("security-reviewer", Verdict.APPROVE)

    def test_verdict_complete_after_recording(self):
        team = self._team()
        self.assertFalse(team.verdicts["product-owner"].is_complete)
        team.record_verdict("product-owner", Verdict.APPROVE)
        self.assertTrue(team.verdicts["product-owner"].is_complete)


# ── Gate enforcement ───────────────────────────────────────────────────────────

class TestGateEnforcement(unittest.TestCase):
    def _team(self) -> ActiveTeam:
        return assemble_team_from_signals(StorySignals())

    def _approve_all(self, team: ActiveTeam) -> None:
        for role in team.blocking_roles:
            team.record_verdict(role.name, Verdict.APPROVE, summary="ok")

    def test_gate_blocked_when_no_verdicts(self):
        team = self._team()
        result = team.check_gate()
        self.assertFalse(result.can_advance)
        self.assertEqual(result.reason, "pending_reviews")

    def test_gate_blocked_when_any_pending(self):
        team = self._team()
        # Approve all except QA
        for role in team.blocking_roles:
            if role.name != "qa-engineer":
                team.record_verdict(role.name, Verdict.APPROVE)
        result = team.check_gate()
        self.assertFalse(result.can_advance)
        self.assertIn("QA Engineer", result.pending_roles)

    def test_gate_blocked_by_product_owner_block(self):
        team = self._team()
        self._approve_all(team)
        team.record_verdict("product-owner", Verdict.BLOCK,
                            blocking_findings=["out of scope"])
        result = team.check_gate()
        self.assertFalse(result.can_advance)
        self.assertEqual(result.reason, "blocked_by_role")

    def test_gate_advances_when_all_blocking_roles_approve(self):
        team = self._team()
        self._approve_all(team)
        result = team.check_gate()
        self.assertTrue(result.can_advance)

    def test_implementer_approval_not_required_for_gate(self):
        team = self._team()
        # Approve all blocking roles but leave implementer pending
        for role in team.blocking_roles:
            team.record_verdict(role.name, Verdict.APPROVE)
        # Implementer not in blocking_roles — gate should pass
        result = team.check_gate()
        self.assertTrue(result.can_advance)

    def test_approve_with_changes_does_not_block(self):
        team = self._team()
        self._approve_all(team)
        team.record_verdict("tech-lead", Verdict.APPROVE_WITH_CHANGES,
                            changes_requested=["extract this into a helper"])
        result = team.check_gate()
        self.assertTrue(result.can_advance)

    def test_error_message_lists_pending_roles(self):
        team = self._team()
        msg = team.check_gate().error_message()
        self.assertIn("Product Owner", msg)

    def test_error_message_lists_blocking_roles(self):
        team = self._team()
        self._approve_all(team)
        team.record_verdict("product-owner", Verdict.BLOCK,
                            blocking_findings=["wrong scope"])
        msg = team.check_gate().error_message()
        self.assertIn("Product Owner", msg)

    def test_summary_output_non_empty(self):
        team = self._team()
        self.assertTrue(team.summary())


# ── StorySignals.from_path_result ──────────────────────────────────────────────

class TestStorySignalsFromPathResult(unittest.TestCase):
    def test_builds_from_path_result(self):
        from shapeitup.ml.path_classifier import classify_path
        result = classify_path(
            "Implement JWT auth endpoint with Redis session store. "
            "Depends on: Story 1."
        )
        signals = StorySignals.from_path_result(result)
        self.assertIsInstance(signals.flagged, bool)
        self.assertIsInstance(signals.security_signal, bool)
        self.assertGreater(signals.word_count, 0)

    def test_security_story_sets_security_signal(self):
        from shapeitup.ml.path_classifier import classify_path
        result = classify_path("Add OAuth2 token rotation with JWT and secret key management")
        signals = StorySignals.from_path_result(result)
        self.assertTrue(signals.security_signal)


if __name__ == "__main__":
    unittest.main()


# ── check_gate: artifact-based gate (new behaviour) ────────────────────────────

class TestCheckGateArtifactBased(unittest.TestCase):
    """
    When workflow_dir + stage are provided:
    - Artifact on disk → role approved (no in-memory verdict required)
    - Artifact on disk + BLOCK verdict → blocking
    - Artifact missing → pending regardless of in-memory verdict
    """

    def _team(self):
        from shapeitup.core.team import assemble_team_from_signals, StorySignals
        return assemble_team_from_signals(StorySignals())

    def test_artifact_on_disk_clears_gate(self, tmp_path=None):
        import tempfile, os
        from pathlib import Path
        team = self._team()
        with tempfile.TemporaryDirectory() as d:
            wdir = Path(d)
            reviews = wdir / "reviews"
            reviews.mkdir()
            stage = "discuss"
            # Write artifact files for all blocking roles
            for role in team.blocking_roles:
                (reviews / f"{role.name}-review-{stage}.md").write_text(
                    f"## {role.display_name} Review\n### Verdict: approve\n"
                )
            gate = team.check_gate(workflow_dir=wdir, stage=stage)
            self.assertTrue(gate.can_advance, f"Expected gate clear, got: {gate.reason}")
            self.assertEqual(gate.pending_roles, [])
            self.assertEqual(gate.blocking_roles, [])

    def test_artifact_missing_is_pending(self):
        import tempfile
        from pathlib import Path
        team = self._team()
        with tempfile.TemporaryDirectory() as d:
            wdir = Path(d)
            # No artifacts written at all
            gate = team.check_gate(workflow_dir=wdir, stage="discuss")
            self.assertFalse(gate.can_advance)
            self.assertEqual(gate.reason, "pending_reviews")
            self.assertGreater(len(gate.pending_roles), 0)

    def test_artifact_present_plus_block_verdict_is_blocking(self):
        import tempfile
        from pathlib import Path
        from shapeitup.core.team import Verdict, RoleVerdict
        team = self._team()
        with tempfile.TemporaryDirectory() as d:
            wdir = Path(d)
            reviews = wdir / "reviews"
            reviews.mkdir()
            stage = "discuss"
            # Write artifacts for all blocking roles
            for role in team.blocking_roles:
                (reviews / f"{role.name}-review-{stage}.md").write_text("done")
            # Record a BLOCK verdict for product-owner
            team.record_verdict(
                role_name="product-owner",
                verdict=Verdict.BLOCK,
                summary="Scope is wrong",
                blocking_findings=["Out-of-scope feature included"],
            )
            gate = team.check_gate(workflow_dir=wdir, stage=stage)
            self.assertFalse(gate.can_advance)
            self.assertEqual(gate.reason, "blocked_by_role")
            self.assertTrue(any("Product Owner" in r for r in gate.blocking_roles))

    def test_partial_artifacts_pending_for_missing_roles(self):
        import tempfile
        from pathlib import Path
        team = self._team()
        blocking = team.blocking_roles
        if len(blocking) < 2:
            self.skipTest("Need at least 2 blocking roles")
        with tempfile.TemporaryDirectory() as d:
            wdir = Path(d)
            reviews = wdir / "reviews"
            reviews.mkdir()
            stage = "discuss"
            # Write artifact for only the first blocking role
            first = blocking[0]
            (reviews / f"{first.name}-review-{stage}.md").write_text("done")
            gate = team.check_gate(workflow_dir=wdir, stage=stage)
            self.assertFalse(gate.can_advance)
            # The first role should NOT be pending; the rest should be
            self.assertNotIn(first.display_name, gate.pending_roles)
            self.assertGreater(len(gate.pending_roles), 0)

    def test_no_workflow_dir_falls_back_to_verdict_check(self):
        """Without workflow_dir, gate requires in-memory verdicts."""
        from shapeitup.core.team import Verdict
        team = self._team()
        # No verdicts recorded, no workflow_dir → all pending
        gate = team.check_gate()
        self.assertFalse(gate.can_advance)
        self.assertGreater(len(gate.pending_roles), 0)


# ── review-sync in ALWAYS_ALLOWED ──────────────────────────────────────────────

class TestReviewSyncAlwaysAllowed(unittest.TestCase):
    def test_review_sync_allowed_at_every_stage(self):
        from shapeitup.core.transitions import Stage, allowed_commands_for as get_allowed_commands
        for stage in Stage:
            allowed = get_allowed_commands(stage)
            self.assertIn(
                "review-sync", allowed,
                f"review-sync should be ALWAYS_ALLOWED but missing from {stage.value}"
            )

    def test_stage_plan_allowed_at_every_stage(self):
        from shapeitup.core.transitions import Stage, allowed_commands_for as get_allowed_commands
        for stage in Stage:
            self.assertIn("stage-plan", get_allowed_commands(stage))

    def test_impl_schedule_allowed_at_every_stage(self):
        from shapeitup.core.transitions import Stage, allowed_commands_for as get_allowed_commands
        for stage in Stage:
            self.assertIn("impl-schedule", get_allowed_commands(stage))
