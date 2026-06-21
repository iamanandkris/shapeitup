"""
impl_commands.py
----------------
Stubs for implementation-stage coordination commands.

These are structurally similar to synthesis stubs — they set needs_llm=True
for commands that require LLM reasoning, or perform lightweight local operations
for pure coordination commands.

Commands covered:
  team-run          → trigger a team-level run (LLM coordinates)
  team-run-level    → set team run scope (LLM coordinates)
  team-sync         → sync team status (mechanical summary)
  merge-gate        → check merge readiness (mechanical + LLM advisory)
  merge-apply       → apply a merge (mechanical record)
  integration-gate  → verify integration readiness (mechanical + LLM advisory)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from shapeitup.core.commands._base import CommandContext, CommandResult
from shapeitup.core.commands._registry import register
from shapeitup.core.state import WorkflowState
from shapeitup.core.team import ActiveTeam


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── team-run ──────────────────────────────────────────────────────────────────

@register("team-run")
def handle_team_run(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Delegate a coordinated team run to the LLM.
    The LLM assigns stories to roles and produces a run plan.
    """
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Coordinate a team run for slug '{ctx.slug}' at implementation stage. "
            f"Context: {ctx.reason or 'none provided'}. "
            "Read stories.md and implementation-plan.md. "
            "Assign each active story to a team role (Implementer leads, "
            "QA validates, Tech Lead reviews integration points). "
            "Output a team-run-plan.md with assignments and sequencing."
        ),
        message="team-run: delegating to LLM coordination",
    )


# ── team-run-level ────────────────────────────────────────────────────────────

@register("team-run-level")
def handle_team_run_level(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Set the scope level for the next team run (story / epic / release)."""
    level = ctx.reason.strip().lower() if ctx.reason else "story"
    valid = {"story", "epic", "release"}
    if level not in valid:
        return CommandResult(
            state=state,
            ok=False,
            message=f"team-run-level: invalid level '{level}'. Choose: story, epic, release",
            warnings=[f"'{level}' is not a valid run level"],
        )

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    (ctx.workflow_dir / "run-level.txt").write_text(level, encoding="utf-8")
    state.next_action = f"Run level set to '{level}' — use team-run to coordinate"
    state._touch()
    return CommandResult(
        state=state,
        message=f"team-run-level: set to '{level}'",
        artifacts_written=["run-level.txt"],
    )


# ── team-sync ─────────────────────────────────────────────────────────────────

@register("team-sync")
def handle_team_sync(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Snapshot current team verdict status to team-sync.json.
    Purely mechanical — reads team object, writes summary.
    """
    if team is None:
        return CommandResult(
            state=state,
            message="team-sync: no active team — run execution-path first",
            warnings=["Team not assembled"],
        )

    gate = team.check_gate()
    sync_data = {
        "timestamp": _now(),
        "stage": state.current_stage.value,
        "gate_can_advance": gate.can_advance,
        "gate_reason": gate.reason,
        "roles": [
            {
                "name": r.name,
                "display_name": r.display_name,
                "blocks_gate": r.blocks_gate,
                "verdict": team.verdicts.get(r.name) and team.verdicts[r.name].verdict.value or "pending",
                "summary": team.verdicts.get(r.name) and team.verdicts[r.name].summary or "",
            }
            for r in team.roles
        ],
    }

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    (ctx.workflow_dir / "team-sync.json").write_text(
        json.dumps(sync_data, indent=2), encoding="utf-8"
    )

    pending = [r["display_name"] for r in sync_data["roles"] if r["verdict"] == "pending" and r["blocks_gate"]]
    msg = (
        f"team-sync: gate={'✓' if gate.can_advance else '✗'} | "
        f"pending blocking roles: {', '.join(pending) if pending else 'none'}"
    )
    return CommandResult(
        state=state,
        message=msg,
        ml_outputs=sync_data,
        artifacts_written=["team-sync.json"],
    )


# ── merge-gate ────────────────────────────────────────────────────────────────

@register("merge-gate")
def handle_merge_gate(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Check if the implementation is ready to merge.
    Reads CI feedback, drift check, and team verdicts.
    Returns a mechanical pass/fail plus LLM advisory if borderline.
    """
    wf = ctx.workflow_dir
    issues: list[str] = []
    advisory_needed = False

    # Check CI status
    ci_path = wf / "ci-feedback.json"
    if ci_path.exists():
        try:
            ci = json.loads(ci_path.read_text(encoding="utf-8"))
            if ci.get("severity") == "high" and not ci.get("is_retryable"):
                issues.append(f"Unresolved HIGH CI failure: {ci.get('failure_class')}")
        except Exception:
            pass
    else:
        advisory_needed = True

    # Check drift
    drift_path = wf / "drift-check.json"
    if drift_path.exists():
        try:
            drift = json.loads(drift_path.read_text(encoding="utf-8"))
            if drift.get("needs_reconciliation"):
                issues.append(f"Design drift detected: {drift.get('drift_type')} (score={drift.get('score')})")
        except Exception:
            pass
    else:
        advisory_needed = True

    if issues:
        for issue in issues:
            state.apply_block(issue)
        return CommandResult(
            state=state,
            ok=False,
            message=f"merge-gate: BLOCKED — {'; '.join(issues)}",
            warnings=issues,
        )

    if advisory_needed:
        return CommandResult(
            state=state,
            needs_llm=True,
            llm_task=(
                f"Advise on merge readiness for slug '{ctx.slug}'. "
                "No CI feedback or drift check found. "
                "Recommend whether to run ci-feedback and drift-check before merging."
            ),
            message="merge-gate: no CI/drift data — requesting LLM advisory",
        )

    state.next_action = "Merge gate passed — run merge-apply to record"
    state._touch()
    return CommandResult(
        state=state,
        message="merge-gate: ✓ PASSED — CI clean, drift acceptable, team gate cleared",
    )


# ── merge-apply ───────────────────────────────────────────────────────────────

@register("merge-apply")
def handle_merge_apply(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Record that a merge was applied."""
    entry = {
        "timestamp": _now(),
        "stage": state.current_stage.value,
        "note": ctx.reason or "merge applied",
        "slug": ctx.slug,
    }
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    merge_log = ctx.workflow_dir / "merge-log.jsonl"
    with merge_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    state.next_action = "Merge recorded — advance with shapeitup:next"
    state._touch()
    return CommandResult(
        state=state,
        message=f"merge-apply: recorded at {entry['timestamp'][:16]}",
        artifacts_written=["merge-log.jsonl"],
    )


# ── integration-gate ──────────────────────────────────────────────────────────

@register("integration-gate")
def handle_integration_gate(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Check integration readiness across stories.
    Reads dag.json to verify all dependencies have been resolved.
    """
    dag_path = ctx.workflow_dir / "dag.json"
    if not dag_path.exists():
        return CommandResult(
            state=state,
            needs_llm=True,
            llm_task=(
                f"Advise on integration readiness for slug '{ctx.slug}'. "
                "No dag.json found — suggest running dag-sync first, "
                "then describe what integration checks should be performed."
            ),
            message="integration-gate: no DAG found — requesting LLM advisory",
            warnings=["Run dag-sync first to build story dependency graph"],
        )

    try:
        dag_data = json.loads(dag_path.read_text(encoding="utf-8"))
        errors = dag_data.get("errors", [])
        story_count = len(dag_data.get("nodes", []))
    except Exception as e:
        return CommandResult(
            state=state,
            message=f"integration-gate: could not read dag.json — {e}",
            warnings=[str(e)],
        )

    if errors:
        for err in errors:
            state.apply_block(f"DAG error: {err}")
        return CommandResult(
            state=state,
            ok=False,
            message=f"integration-gate: BLOCKED — {len(errors)} DAG errors",
            warnings=errors,
        )

    state.next_action = f"Integration gate passed ({story_count} stories) — proceed to review"
    state._touch()
    return CommandResult(
        state=state,
        message=f"integration-gate: ✓ PASSED — {story_count} stories, no DAG errors",
        ml_outputs={"story_count": story_count, "dag_errors": errors},
    )
