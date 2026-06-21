"""
stage_planner.py
----------------
Computes the phase-based execution plan for any workflow stage.

Each stage has up to 3 phases:
  Phase 1 — Generation (sequential):   synthesis command produces the stage artifact
  Phase 2 — Parallel review:           all active blocking roles run their review agents
  Phase 3 — Gate check:                Python verifies artifacts exist, verdicts recorded

The plan is returned as a JSON-serialisable dict consumed by the skill,
which uses it to spawn parallel Claude agents for Phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapeitup.core.state import Stage


# ── Stage → synthesis command mapping ─────────────────────────────────────────

STAGE_SYNTHESIS: dict[Stage, dict[str, str]] = {
    Stage.DISCUSS: {
        "command": "capability-synth",
        "description": "Generate capabilities.md — in-scope features, out-of-scope items, open questions",
        "artifact": "capabilities.md",
    },
    Stage.CAPABILITY_REVIEW: {
        "command": "capability-synth",
        "description": "Refine capabilities.md based on initial review findings",
        "artifact": "capabilities.md",
    },
    Stage.EPIC_SHAPING: {
        "command": "design-synth",
        "description": "Generate design-seed.md — problem statement, approach, interfaces, risks",
        "artifact": "design-seed.md",
    },
    Stage.STORY_SLICING: {
        "command": "story-synth",
        "description": "Generate stories.md — atomic stories with ACs, deps, test hints",
        "artifact": "stories.md",
    },
    Stage.STORY_ENRICHMENT: {
        "command": "story-enrichment-synth",
        "description": "Enrich stories.md with edge cases, validation, cross-cutting concerns",
        "artifact": "stories.md",
    },
    Stage.SPEC_AUTHORING: {
        "command": "openspec-synth",
        "description": "Generate openspec.md — API contracts, data models, auth",
        "artifact": "openspec.md",
    },
    Stage.IMPLEMENTATION_PLANNING: {
        "command": "implementation-plan-synth",
        "description": "Generate implementation-plan.md — build order, parallel tracks, DoD",
        "artifact": "implementation-plan.md",
    },
    Stage.REVIEW: {
        "command": "feedback-synth",
        "description": "Synthesise team review notes into feedback-synthesis.md",
        "artifact": "feedback-synthesis.md",
    },
    Stage.RELEASE_PLANNING: {
        "command": "feedback-synth",
        "description": "Final release readiness synthesis",
        "artifact": "feedback-synthesis.md",
    },
}


# ── Phase dataclasses ──────────────────────────────────────────────────────────

@dataclass
class PlanTask:
    role: str                   # "system" for generation tasks, role name for reviews
    command: str
    description: str
    artifact_path: str          # relative to workflow_dir
    depends_on: list[str] = field(default_factory=list)  # artifact paths that must exist first
    needs_llm: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "command": self.command,
            "description": self.description,
            "artifact_path": self.artifact_path,
            "depends_on": self.depends_on,
            "needs_llm": self.needs_llm,
        }


@dataclass
class Phase:
    phase: int
    label: str
    parallel: bool
    tasks: list[PlanTask]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "label": self.label,
            "parallel": self.parallel,
            "tasks": [t.to_dict() for t in self.tasks],
        }


@dataclass
class StagePlan:
    stage: str
    phases: list[Phase]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "phases": [p.to_dict() for p in self.phases],
            "notes": self.notes,
        }


# ── Planner ────────────────────────────────────────────────────────────────────

def compute_stage_plan(
    stage: Stage,
    team: "ActiveTeam",  # type: ignore[name-defined]  # imported by caller
    workflow_dir: "Path | None" = None,  # type: ignore[name-defined]
) -> StagePlan:
    """
    Return the execution plan for a given stage and team.

    Implementation stage has a special plan (see compute_impl_plan).
    All other stages follow the 3-phase pattern.
    """
    if stage == Stage.IMPLEMENTATION:
        return _impl_stage_plan(team)

    phases: list[Phase] = []
    stage_str = stage.value
    synth = STAGE_SYNTHESIS.get(stage)

    # Phase 1: Generation (sequential)
    if synth:
        phases.append(Phase(
            phase=1,
            label="Generate stage artifact",
            parallel=False,
            tasks=[PlanTask(
                role="system",
                command=synth["command"],
                description=synth["description"],
                artifact_path=synth["artifact"],
                depends_on=[],
                needs_llm=True,
            )],
        ))

    # Phase 2: Parallel role reviews
    review_tasks: list[PlanTask] = []
    for role in team.blocking_roles:
        tasks_for_stage = role.tasks_for_stage(stage_str)
        # Use first matching task (should be the *-review one, not impl-specific)
        task = next(
            (t for t in tasks_for_stage if "impl" not in t.command and "test" not in t.command),
            None,
        )
        if not task:
            # Fallback: any task for this stage
            task = tasks_for_stage[0] if tasks_for_stage else None
        if not task:
            continue

        artifact = task.artifact_name.replace("{stage}", stage_str)
        review_tasks.append(PlanTask(
            role=role.name,
            command=task.command,
            description=task.description,
            artifact_path=f"reviews/{artifact}",
            depends_on=[synth["artifact"]] if synth else [],
            needs_llm=True,
        ))

    if review_tasks:
        phases.append(Phase(
            phase=2,
            label="Parallel role reviews",
            parallel=True,
            tasks=review_tasks,
        ))

    # Phase 3: Gate check (mechanical)
    phases.append(Phase(
        phase=len(phases) + 1,
        label="Gate check",
        parallel=False,
        tasks=[PlanTask(
            role="system",
            command="review-sync",
            description="Verify all review artifacts exist and no role is blocking",
            artifact_path="state.json",
            depends_on=[t.artifact_path for t in review_tasks],
            needs_llm=False,
        )],
    ))

    notes = []
    if not synth:
        notes.append(f"No synthesis command defined for stage '{stage_str}' — starting directly with reviews")

    return StagePlan(stage=stage_str, phases=phases, notes=notes)


def _impl_stage_plan(team: "ActiveTeam") -> StagePlan:  # type: ignore[name-defined]
    """
    Implementation stage plan — references impl-schedule for per-story TDD phases.
    The full per-story breakdown is in ImplScheduler.
    """
    return StagePlan(
        stage="implementation",
        phases=[
            Phase(
                phase=1,
                label="Build story schedule from DAG",
                parallel=False,
                tasks=[PlanTask(
                    role="system",
                    command="impl-schedule",
                    description=(
                        "Read dag.json, topologically sort stories into parallel groups. "
                        "Returns ordered groups — stories in the same group have no "
                        "inter-dependencies and can be implemented in parallel."
                    ),
                    artifact_path="impl-schedule.json",
                    needs_llm=False,
                )],
            ),
            Phase(
                phase=2,
                label="Per-story TDD + pair programming (run group-by-group per schedule)",
                parallel=True,
                tasks=[PlanTask(
                    role="system",
                    command="pair-implement",
                    description=(
                        "For each story group (parallel): "
                        "1. QA writes failing tests (qa-test-spec). "
                        "2. Pair programming: Agent A proposes, Agent B challenges, "
                        "   max 3 rounds, consensus required (pair-propose / pair-challenge). "
                        "3. Parallel validation: QA validates tests pass (qa-validate) + "
                        "   Tech Lead reviews implementation (tl-impl-review) + "
                        "   Security scan if activated (security-scan)."
                    ),
                    artifact_path="impl-schedule.json",
                    depends_on=["impl-schedule.json"],
                    needs_llm=True,
                )],
            ),
        ],
        notes=[
            "Use impl-schedule to get story groups, then implement each group in parallel.",
            "Within each story: QA writes tests first (TDD), then pair programming, then validation.",
            "Stories within a group are independent — spawn one agent per story in the group.",
        ],
    )
