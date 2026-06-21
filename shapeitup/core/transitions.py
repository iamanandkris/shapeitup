"""
transitions.py
--------------
Explicit stage-command transition table for shapeitup.

Integrates with team.py: gate advancement requires BOTH
  1. check_transition() → allowed
  2. ActiveTeam.check_gate() → can_advance

Neither alone is sufficient. A command can be in the transition table
but still blocked by a pending or blocking role verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from shapeitup.core.state import Stage, GATED_STAGES


# ── Always-allowed commands ────────────────────────────────────────────────────
# These bypass the per-stage table entirely.

ALWAYS_ALLOWED: Final[frozenset[str]] = frozenset({
    "actions",
    "override",
    "memory-record",
    "debt-record",
    "accounting-record",
    "reconcile",
    "dag-sync",
})

# ── Per-stage allowed commands ─────────────────────────────────────────────────

_SYNTH_COMMON: Final[frozenset[str]] = frozenset({
    "feedback-synth",
    "issue-advisor",
})

STAGE_COMMANDS: Final[dict[Stage, frozenset[str]]] = {
    Stage.DISCUSS: frozenset({
        "approve", "reject", "next", "capability-synth",
    }),
    Stage.CAPABILITY_REVIEW: frozenset({
        "approve", "reject", "rework-item", "capability-synth",
        *_SYNTH_COMMON,
    }),
    Stage.EPIC_SHAPING: frozenset({
        "approve", "reject", "rework-item", "design-synth",
        "staff", *_SYNTH_COMMON,
    }),
    Stage.STORY_SLICING: frozenset({
        "approve", "reject", "rework-item", "story-synth",
        "staff", "assign", *_SYNTH_COMMON,
    }),
    Stage.STORY_ENRICHMENT: frozenset({
        "approve", "reject", "rework-item", "story-enrichment-synth",
        "assign", *_SYNTH_COMMON,
    }),
    Stage.SPEC_AUTHORING: frozenset({
        "approve", "reject", "rework-item",
        "openspec-sync", "openspec-synth", "implementation-plan-synth",
        "assign", *_SYNTH_COMMON,
    }),
    Stage.IMPLEMENTATION_PLANNING: frozenset({
        "proceed-only", "defer", "next", "reject", "rework-item",
        "replan", "implementation-plan-synth", *_SYNTH_COMMON,
    }),
    Stage.IMPLEMENTATION: frozenset({
        "next", "ci-feedback", "team-run", "team-run-level",
        "team-sync", "merge-gate", "merge-apply", "integration-gate",
        "execution-path", "challenge", "review-sync",
        "refine", "reject", "replan", "verify-fix", *_SYNTH_COMMON,
    }),
    Stage.REVIEW: frozenset({
        "approve", "reject", "review-sync", *_SYNTH_COMMON,
    }),
    Stage.RELEASE_PLANNING: frozenset({
        "approve", "reject", *_SYNTH_COMMON,
    }),
    Stage.DONE: frozenset({
        "next",
    }),
}

# Full allowed set per stage = stage-specific ∪ ALWAYS_ALLOWED
ALLOWED: Final[dict[Stage, frozenset[str]]] = {
    stage: (STAGE_COMMANDS.get(stage, frozenset()) | ALWAYS_ALLOWED)
    for stage in Stage
}


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TransitionResult:
    allowed: bool
    stage: Stage
    command: str
    allowed_commands: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""

    def error_message(self) -> str:
        if self.allowed:
            return ""
        stage_specific = sorted(
            self.allowed_commands - ALWAYS_ALLOWED - {"override"}
        )
        universal = sorted(ALWAYS_ALLOWED)
        lines = [
            f"Command '{self.command}' is not allowed in stage '{self.stage.value}'.",
            f"Stage-specific commands: {', '.join(stage_specific) or '(none)'}",
            f"Always available: {', '.join(universal)}",
        ]
        if self.reason:
            lines.insert(1, f"Reason: {self.reason}")
        return "\n".join(lines)


# ── Gate integration result ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FullGateResult:
    """
    Combined result of transition check + team gate check.
    Both must pass for a command to proceed.
    """
    transition_allowed: bool
    team_gate_passed: bool
    transition_result: TransitionResult
    gate_error: str = ""      # from ActiveTeam.check_gate().error_message()

    @property
    def can_proceed(self) -> bool:
        return self.transition_allowed and self.team_gate_passed

    def error_message(self) -> str:
        parts: list[str] = []
        if not self.transition_allowed:
            parts.append(self.transition_result.error_message())
        if not self.team_gate_passed:
            parts.append(self.gate_error)
        return "\n\n".join(parts)


# ── Public API ─────────────────────────────────────────────────────────────────

def check_transition(stage: Stage, command: str) -> TransitionResult:
    """
    Check whether command is allowed in stage.

    override always passes.
    Unknown stages fail-open (forward compatibility).
    """
    if command == "override":
        return TransitionResult(allowed=True, stage=stage, command=command)

    if stage not in ALLOWED:
        return TransitionResult(
            allowed=True, stage=stage, command=command,
            reason=f"stage '{stage.value}' not in transition table (fail-open)",
        )

    allowed_set = ALLOWED[stage]
    if command in allowed_set:
        return TransitionResult(
            allowed=True, stage=stage, command=command,
            allowed_commands=allowed_set,
        )

    return TransitionResult(
        allowed=False, stage=stage, command=command,
        allowed_commands=allowed_set,
        reason=f"'{command}' has no defined transition from '{stage.value}'",
    )


def check_full_gate(
    stage: Stage,
    command: str,
    team: "object | None" = None,
) -> FullGateResult:
    """
    Run both transition check and team gate check.

    Args:
        stage:   Current workflow stage.
        command: Command being requested.
        team:    ActiveTeam instance (optional). If None, team gate is skipped.

    Returns:
        FullGateResult — both checks must pass for can_proceed=True.
    """
    transition = check_transition(stage, command)

    # Team gate only relevant for advance commands at gated stages
    if team is not None and command in {"approve", "next"} and stage in GATED_STAGES:
        gate_check = team.check_gate()  # type: ignore[union-attr]
        return FullGateResult(
            transition_allowed=transition.allowed,
            team_gate_passed=gate_check.can_advance,
            transition_result=transition,
            gate_error=gate_check.error_message() if not gate_check.can_advance else "",
        )

    return FullGateResult(
        transition_allowed=transition.allowed,
        team_gate_passed=True,
        transition_result=transition,
    )


def allowed_commands_for(stage: Stage) -> frozenset[str]:
    """Return all commands allowed in a stage (for actions menu)."""
    return ALLOWED.get(stage, ALWAYS_ALLOWED)
