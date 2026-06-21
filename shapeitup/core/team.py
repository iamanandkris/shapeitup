"""
team.py
-------
Structural team model for shapeitup.

Team structure is defined in code, not markdown. This means:
  - Role activation is computed from ML classifier outputs, not read from a file
  - Gate advancement is blocked in code if required roles haven't reviewed
  - A Product Owner or Tech Lead block cannot be bypassed except via override
  - The LLM's job is to produce review content — it never decides team structure

Roles:
  product-owner       Always active. Liaison between user intent and the team.
                      Owns: scope, user value, AC-to-user-need mapping.
                      Blocks gate: yes.

  tech-lead           Always active. Architecture, boundaries, sequencing.
                      Includes ML architecture lens when ML artifacts are touched.
                      Blocks gate: yes.

  implementer         Always active (1..N lanes with disjoint write paths).
                      Owns: code, feasibility, file ownership.
                      Blocks gate: no (implementer completion advances, not blocks).

  qa-engineer         Always active. Test coverage, AC verification, fallback paths.
                      Includes ML test coverage lens when classifiers are involved.
                      Blocks gate: yes.

  security-reviewer   Conditional. Activates when path_classifier flags security_signal
                      or story is 'flagged' with interface_signals >= 3.
                      Blocks gate: yes.

Gate advancement rule:
  A gate may only advance when ALL of:
    1. Every active blocking role has submitted a verdict
    2. No active blocking role verdict is "block"
    3. workflow_transitions.py allows the command in the current stage
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final


# ── Enums ──────────────────────────────────────────────────────────────────────

class Verdict(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_CHANGES = "approve-with-changes"
    BLOCK = "block"
    PENDING = "pending"       # review not yet submitted
    NOT_REQUIRED = "not-required"  # role not active for this story


class ReviewArtifact(str, Enum):
    """Artifacts a role is expected to review."""
    CAPABILITIES   = "capabilities.md"
    STORIES        = "stories.md"
    DESIGN_SLICE   = "design-slice.md"
    IMPLEMENTATION_PLAN = "implementation-plan.md"
    SPEC           = "spec.md"
    VERIFY_FIX     = "verify-fix.md"
    CI_FEEDBACK    = "ci-feedback.md"
    MERGE_GATE     = "merge-gate.md"


# ── Activation condition keys (produced by ML classifiers) ────────────────────

class ActivationCondition(str, Enum):
    SECURITY_SIGNAL     = "security_signal"       # path_classifier.features.security_signal
    FLAGGED             = "flagged"               # path_classifier result == "flagged"
    MULTI_SERVICE       = "multi_service_signal"  # path_classifier.features.multi_service_signal
    HIGH_INTERFACE      = "high_interface"        # interface_signals >= 3
    ALWAYS              = "always"                # unconditional


# ── Role definition ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Role:
    """
    Immutable role definition.

    activation_conditions: ANY of these being true activates the role.
                           ActivationCondition.ALWAYS → always active.
    blocks_gate:           True = a BLOCK verdict from this role halts gate advancement.
    review_artifacts:      Artifacts this role should review (informational for prompt).
    bias:                  One-line description of review lens (used in LLM prompt only).
    """
    name: str
    display_name: str
    activation_conditions: frozenset[ActivationCondition]
    blocks_gate: bool
    review_artifacts: tuple[ReviewArtifact, ...]
    bias: str

    @property
    def always_active(self) -> bool:
        return ActivationCondition.ALWAYS in self.activation_conditions

    def is_active_for(self, signals: "StorySignals") -> bool:
        """Return True if this role should be on the team for a given story."""
        for condition in self.activation_conditions:
            if condition == ActivationCondition.ALWAYS:
                return True
            if condition == ActivationCondition.SECURITY_SIGNAL and signals.security_signal:
                return True
            if condition == ActivationCondition.FLAGGED and signals.flagged:
                return True
            if condition == ActivationCondition.MULTI_SERVICE and signals.multi_service_signal:
                return True
            if condition == ActivationCondition.HIGH_INTERFACE and signals.interface_signals >= 3:
                return True
        return False


# ── Signal bag (produced by ML classifiers, passed to team assembly) ───────────

@dataclass(frozen=True)
class StorySignals:
    """
    ML-derived signals for a story. Produced by path_classifier.
    Never read from markdown — always computed fresh from story text.
    """
    flagged: bool = False
    security_signal: bool = False
    multi_service_signal: bool = False
    interface_signals: int = 0
    dep_count: int = 0
    word_count: int = 0
    path_confidence: float = 0.0

    @classmethod
    def from_path_result(cls, result: object) -> "StorySignals":
        """Build from a path_classifier.PathResult object."""
        f = getattr(result, "features", None)
        return cls(
            flagged=getattr(result, "path_type", "simple") == "flagged",
            security_signal=getattr(f, "security_signal", False) if f else False,
            multi_service_signal=getattr(f, "multi_service_signal", False) if f else False,
            interface_signals=getattr(f, "interface_signals", 0) if f else 0,
            dep_count=getattr(f, "dep_count", 0) if f else 0,
            word_count=getattr(f, "word_count", 0) if f else 0,
            path_confidence=getattr(result, "confidence", 0.0),
        )


# ── Default role catalogue ─────────────────────────────────────────────────────

PRODUCT_OWNER = Role(
    name="product-owner",
    display_name="Product Owner",
    activation_conditions=frozenset({ActivationCondition.ALWAYS}),
    blocks_gate=True,
    review_artifacts=(
        ReviewArtifact.CAPABILITIES,
        ReviewArtifact.STORIES,
        ReviewArtifact.DESIGN_SLICE,
    ),
    bias=(
        "Challenges user value, scope, acceptance criteria mapping to real user need, "
        "and non-goals. Asks: does this story solve what the user actually described?"
    ),
)

TECH_LEAD = Role(
    name="tech-lead",
    display_name="Tech Lead",
    activation_conditions=frozenset({ActivationCondition.ALWAYS}),
    blocks_gate=True,
    review_artifacts=(
        ReviewArtifact.DESIGN_SLICE,
        ReviewArtifact.IMPLEMENTATION_PLAN,
        ReviewArtifact.SPEC,
    ),
    bias=(
        "Challenges architecture, boundaries, dependency sequencing, integration risk, "
        "and ML module selection/fallback design when relevant."
    ),
)

IMPLEMENTER = Role(
    name="implementer",
    display_name="Implementer",
    activation_conditions=frozenset({ActivationCondition.ALWAYS}),
    blocks_gate=False,   # implementer completion advances; it doesn't block
    review_artifacts=(
        ReviewArtifact.IMPLEMENTATION_PLAN,
    ),
    bias=(
        "Challenges feasibility, implementation complexity, file ownership conflicts, "
        "and maintainability. Declares Allowed Write Paths explicitly."
    ),
)

QA_ENGINEER = Role(
    name="qa-engineer",
    display_name="QA Engineer",
    activation_conditions=frozenset({ActivationCondition.ALWAYS}),
    blocks_gate=True,
    review_artifacts=(
        ReviewArtifact.STORIES,
        ReviewArtifact.VERIFY_FIX,
        ReviewArtifact.CI_FEEDBACK,
    ),
    bias=(
        "Challenges test coverage, edge cases, fallback paths, acceptance criteria "
        "verifiability, and regression risk. Blocks if any AC has no test evidence."
    ),
)

SECURITY_REVIEWER = Role(
    name="security-reviewer",
    display_name="Security Reviewer",
    activation_conditions=frozenset({
        ActivationCondition.SECURITY_SIGNAL,
        ActivationCondition.FLAGGED,
        ActivationCondition.HIGH_INTERFACE,
    }),
    blocks_gate=True,
    review_artifacts=(
        ReviewArtifact.SPEC,
        ReviewArtifact.MERGE_GATE,
    ),
    bias=(
        "Challenges auth flows, input validation, data exposure, secret handling, "
        "path traversal, subprocess safety, and API boundary security."
    ),
)

# Ordered: PO first (user intent), then Tech Lead, Implementer(s), QA, Security
DEFAULT_ROLES: Final[tuple[Role, ...]] = (
    PRODUCT_OWNER,
    TECH_LEAD,
    IMPLEMENTER,
    QA_ENGINEER,
    SECURITY_REVIEWER,
)


# ── Verdict record ─────────────────────────────────────────────────────────────

@dataclass
class RoleVerdict:
    role_name: str
    verdict: Verdict = Verdict.PENDING
    summary: str = ""
    blocking_findings: list[str] = field(default_factory=list)
    changes_requested: list[str] = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.verdict == Verdict.BLOCK

    @property
    def is_complete(self) -> bool:
        return self.verdict != Verdict.PENDING


# ── Active team assembly ───────────────────────────────────────────────────────

@dataclass
class ActiveTeam:
    """
    The team assembled for a specific story, driven by ML signals.
    Tracks verdicts and enforces gate advancement rules.
    """
    signals: StorySignals
    roles: list[Role] = field(default_factory=list)
    verdicts: dict[str, RoleVerdict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.roles:
            self.roles = [
                r for r in DEFAULT_ROLES
                if r.is_active_for(self.signals)
            ]
        for role in self.roles:
            if role.name not in self.verdicts:
                self.verdicts[role.name] = RoleVerdict(role_name=role.name)

    @property
    def active_role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def blocking_roles(self) -> list[Role]:
        return [r for r in self.roles if r.blocks_gate]

    def record_verdict(
        self,
        role_name: str,
        verdict: Verdict,
        summary: str = "",
        blocking_findings: list[str] | None = None,
        changes_requested: list[str] | None = None,
    ) -> None:
        """Record a role's review verdict."""
        if role_name not in self.verdicts:
            raise ValueError(
                f"Role '{role_name}' is not active for this story. "
                f"Active roles: {self.active_role_names}"
            )
        self.verdicts[role_name] = RoleVerdict(
            role_name=role_name,
            verdict=verdict,
            summary=summary,
            blocking_findings=blocking_findings or [],
            changes_requested=changes_requested or [],
        )

    # ── Gate check ─────────────────────────────────────────────────────────────

    @dataclass(frozen=True)
    class GateCheckResult:
        can_advance: bool
        reason: str
        pending_roles: list[str]
        blocking_roles: list[str]

        def error_message(self) -> str:
            lines: list[str] = []
            if self.pending_roles:
                lines.append(
                    f"Waiting for review from: {', '.join(self.pending_roles)}"
                )
            if self.blocking_roles:
                lines.append(
                    f"Blocked by: {', '.join(self.blocking_roles)}"
                )
            return "\n".join(lines) if lines else "Gate check passed."

    def check_gate(self) -> "ActiveTeam.GateCheckResult":
        """
        Return whether the gate can advance.

        Rules:
          1. Every active blocking role must have submitted a verdict.
          2. No active blocking role verdict may be BLOCK.
        """
        pending: list[str] = []
        blocking: list[str] = []

        for role in self.blocking_roles:
            rv = self.verdicts.get(role.name)
            if rv is None or not rv.is_complete:
                pending.append(role.display_name)
            elif rv.is_blocking:
                blocking.append(
                    f"{role.display_name}"
                    + (f": {rv.blocking_findings[0]}" if rv.blocking_findings else "")
                )

        if pending:
            return self.GateCheckResult(
                can_advance=False,
                reason="pending_reviews",
                pending_roles=pending,
                blocking_roles=blocking,
            )
        if blocking:
            return self.GateCheckResult(
                can_advance=False,
                reason="blocked_by_role",
                pending_roles=[],
                blocking_roles=blocking,
            )
        return self.GateCheckResult(
            can_advance=True,
            reason="all_blocking_roles_approved",
            pending_roles=[],
            blocking_roles=[],
        )

    def summary(self) -> str:
        """Human-readable team status for logging/display."""
        lines = [f"Active team ({len(self.roles)} roles):"]
        for role in self.roles:
            rv = self.verdicts.get(role.name)
            verdict_str = rv.verdict.value if rv else "pending"
            gate_marker = " [blocks gate]" if role.blocks_gate else ""
            lines.append(f"  {role.display_name}: {verdict_str}{gate_marker}")
        gate = self.check_gate()
        lines.append(f"Gate: {'✓ can advance' if gate.can_advance else '✗ ' + gate.reason}")
        return "\n".join(lines)


# ── Factory ────────────────────────────────────────────────────────────────────

def assemble_team(story_text: str) -> ActiveTeam:
    """
    Assemble the active team for a story by running ML classifiers.

    This is the primary entry point — it computes signals fresh from
    the story text every time. Never reads from team-config.md.
    """
    from shapeitup.ml.path_classifier import classify_path
    result = classify_path(story_text)
    signals = StorySignals.from_path_result(result)
    return ActiveTeam(signals=signals)


def assemble_team_from_signals(signals: StorySignals) -> ActiveTeam:
    """Assemble team from pre-computed signals (e.g., from cached classifier output)."""
    return ActiveTeam(signals=signals)
