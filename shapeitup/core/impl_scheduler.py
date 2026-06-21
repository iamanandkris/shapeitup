"""
impl_scheduler.py
-----------------
DAG-driven story implementation scheduler.

Reads dag.json (produced by dag-sync) and produces an ordered schedule:
  - Stories with no dependencies form Group 1 (run in parallel)
  - Stories whose deps are all in Group 1 form Group 2
  - etc.

Each group contains story implementation plans with TDD phases:
  Phase 1: qa-test-spec  (QA writes failing tests — sequential, must come first)
  Phase 2: pair-implement (pair programming — proposer + challenger)
  Phase 3: parallel validation (qa-validate + tl-impl-review + security-scan if active)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Per-story TDD phases ───────────────────────────────────────────────────────

@dataclass
class StoryPhase:
    phase: int
    label: str
    parallel: bool
    tasks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "label": self.label,
            "parallel": self.parallel,
            "tasks": self.tasks,
        }


@dataclass
class StoryPlan:
    story: str
    phases: list[StoryPhase]

    def to_dict(self) -> dict[str, Any]:
        return {"story": self.story, "phases": [p.to_dict() for p in self.phases]}


@dataclass
class StoryGroup:
    group: int
    stories: list[StoryPlan]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "stories": [s.to_dict() for s in self.stories],
            "notes": self.notes,
        }


@dataclass
class ImplSchedule:
    groups: list[StoryGroup]
    total_stories: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "total_stories": self.total_stories,
            "errors": self.errors,
        }


# ── TDD phase builder ─────────────────────────────────────────────────────────

def _story_tdd_phases(story: str, security_active: bool = False) -> list[StoryPhase]:
    """Build the 3-phase TDD plan for a single story."""
    slug = story.lower().replace(" ", "-").replace(":", "").replace("/", "-")[:40]

    validation_tasks = [
        {
            "role": "qa-engineer",
            "command": "qa-validate",
            "description": f"Confirm all tests pass and ACs are covered for: {story}",
            "artifact_path": f"reviews/qa-validate-{slug}.md",
            "needs_llm": True,
        },
        {
            "role": "tech-lead",
            "command": "tl-impl-review",
            "description": f"Review implementation quality and design conformance for: {story}",
            "artifact_path": f"reviews/tl-impl-review-{slug}.md",
            "needs_llm": True,
        },
    ]

    if security_active:
        validation_tasks.append({
            "role": "security-reviewer",
            "command": "security-scan",
            "description": f"Security scan of implementation for: {story}",
            "artifact_path": f"reviews/security-review-impl-{slug}.md",
            "needs_llm": True,
        })

    return [
        StoryPhase(
            phase=1,
            label="QA writes failing tests (TDD first step)",
            parallel=False,
            tasks=[{
                "role": "qa-engineer",
                "command": "qa-test-spec",
                "description": (
                    f"Write failing test specifications for: {story}. "
                    "Tests must run and fail before any implementation. "
                    "Cover all ACs, happy path, edge cases, error conditions."
                ),
                "artifact_path": f"reviews/qa-test-spec-{slug}.md",
                "needs_llm": True,
            }],
        ),
        StoryPhase(
            phase=2,
            label="Pair programming — Proposer + Challenger reach consensus",
            parallel=False,  # sequential within pair, but pairs across stories are parallel
            tasks=[
                {
                    "role": "implementer-proposer",
                    "command": "pair-propose",
                    "description": (
                        f"Proposer: read the QA test spec and story spec. "
                        f"Propose an implementation for: {story}. "
                        "Declare all files you will write. Explain key design decisions."
                    ),
                    "artifact_path": f"reviews/pair-propose-{slug}.md",
                    "needs_llm": True,
                    "depends_on": [f"reviews/qa-test-spec-{slug}.md"],
                },
                {
                    "role": "implementer-challenger",
                    "command": "pair-challenge",
                    "description": (
                        f"Challenger: read the Proposer's plan for: {story}. "
                        "Challenge assumptions, identify risks, suggest improvements. "
                        "You may agree, request changes, or raise blocking findings."
                    ),
                    "artifact_path": f"reviews/pair-challenge-{slug}.md",
                    "needs_llm": True,
                    "depends_on": [f"reviews/pair-propose-{slug}.md"],
                },
                {
                    "role": "implementer",
                    "command": "pair-implement",
                    "description": (
                        f"Write the final consensus implementation for: {story}. "
                        "Incorporate all Challenger feedback. Make the failing tests pass."
                    ),
                    "artifact_path": f"impl/{slug}/",
                    "needs_llm": True,
                    "depends_on": [f"reviews/pair-challenge-{slug}.md"],
                },
            ],
        ),
        StoryPhase(
            phase=3,
            label="Parallel validation (QA + Tech Lead + Security)",
            parallel=True,
            tasks=validation_tasks,
        ),
    ]


# ── Topological sort ──────────────────────────────────────────────────────────

def _topo_sort(dag: dict[str, list[str]]) -> tuple[list[list[str]], list[str]]:
    """
    Topologically sort stories into parallel groups.
    Returns (groups, errors).
    Group 0 = stories with no dependencies.
    Group N = stories whose deps are all in groups < N.
    """
    errors: list[str] = []
    in_group: dict[str, int] = {}
    remaining = set(dag.keys())
    groups: list[list[str]] = []
    group_idx = 0

    while remaining:
        # Find stories whose deps are all resolved
        ready = [
            s for s in remaining
            if all(dep in in_group for dep in dag.get(s, []))
        ]
        if not ready:
            # Cycle or unresolvable deps
            errors.append(
                f"Cannot resolve dependencies for: {sorted(remaining)} — "
                "possible cycle detected"
            )
            # Emit remaining as a single group to avoid infinite loop
            groups.append(sorted(remaining))
            for s in remaining:
                in_group[s] = group_idx
            break

        groups.append(sorted(ready))
        for s in ready:
            in_group[s] = group_idx
            remaining.discard(s)
        group_idx += 1

    return groups, errors


# ── Main scheduler ────────────────────────────────────────────────────────────

def compute_impl_schedule(
    workflow_dir: Path,
    security_active: bool = False,
) -> ImplSchedule:
    """
    Read dag.json from workflow_dir and produce an ImplSchedule.
    Falls back gracefully if dag.json is missing.
    """
    dag_path = workflow_dir / "dag.json"

    if not dag_path.exists():
        return ImplSchedule(
            groups=[],
            total_stories=0,
            errors=["dag.json not found — run dag-sync first"],
        )

    try:
        dag_data = json.loads(dag_path.read_text(encoding="utf-8"))
    except Exception as e:
        return ImplSchedule(
            groups=[],
            total_stories=0,
            errors=[f"Could not read dag.json: {e}"],
        )

    dag_errors = dag_data.get("errors", [])
    dag: dict[str, list[str]] = dag_data.get("edges", {})

    # Ensure all nodes are in the dag dict (even with no edges)
    for node in dag_data.get("nodes", []):
        if node not in dag:
            dag[node] = []

    if not dag:
        return ImplSchedule(
            groups=[],
            total_stories=0,
            errors=(dag_errors or ["No stories found in dag.json"]),
        )

    ordered_groups, topo_errors = _topo_sort(dag)
    all_errors = dag_errors + topo_errors

    story_groups: list[StoryGroup] = []
    for i, stories in enumerate(ordered_groups):
        plans = [
            StoryPlan(
                story=s,
                phases=_story_tdd_phases(s, security_active=security_active),
            )
            for s in stories
        ]
        notes = []
        if len(stories) > 1:
            notes.append(
                f"Group {i + 1} has {len(stories)} independent stories — "
                "implement in parallel (spawn one agent per story)"
            )
        story_groups.append(StoryGroup(group=i + 1, stories=plans, notes=notes))

    return ImplSchedule(
        groups=story_groups,
        total_stories=sum(len(g.stories) for g in story_groups),
        errors=all_errors,
    )
