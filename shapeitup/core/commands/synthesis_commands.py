"""
synthesis_commands.py
---------------------
Python stubs for all LLM synthesis commands.

These handlers do NOT generate content — that is the skill's job.
Each handler:
  1. Reads existing artifacts for context (metadata only)
  2. Returns needs_llm=True with a precise llm_task description
  3. The skill sees needs_llm=True and performs the actual generation

This keeps the gate enforcement and state machine entirely in Python
while the content layer stays in the LLM.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from shapeitup.core.commands._base import CommandContext, CommandResult
from shapeitup.core.commands._registry import register
from shapeitup.core.state import WorkflowState
from shapeitup.core.team import ActiveTeam


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_hint(workflow_dir: Path, *filenames: str) -> str:
    """Return a comma-separated list of artifacts that already exist."""
    found = [f for f in filenames if (workflow_dir / f).exists()]
    return ", ".join(found) if found else "none"


# ── capability-synth ───────────────────────────────────────────────────────────

@register("capability-synth")
def handle_capability_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(ctx.workflow_dir, "capabilities.md", "design-seed.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Generate capabilities.md for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"Context hint: {ctx.reason or 'none provided'}. "
            "Write: in-scope capabilities, out-of-scope items, open questions."
        ),
        message="capability-synth: delegating to LLM synthesis",
    )


# ── design-synth ───────────────────────────────────────────────────────────────

@register("design-synth")
def handle_design_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(ctx.workflow_dir, "capabilities.md", "design-seed.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Generate design-seed.md for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"Context hint: {ctx.reason or 'none provided'}. "
            "Write: problem statement, proposed approach, key interfaces/contracts, "
            "risks, unknowns."
        ),
        message="design-synth: delegating to LLM synthesis",
    )


# ── story-synth ────────────────────────────────────────────────────────────────

@register("story-synth")
def handle_story_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(ctx.workflow_dir, "capabilities.md", "design-seed.md", "stories.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Generate stories.md for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"Context hint: {ctx.reason or 'none provided'}. "
            "Slice the epic into atomic, independently deployable stories. "
            "Each story: goal, value, ACs (testable), dependencies (Depends-on), "
            "test hints. 5–12 stories typical."
        ),
        message="story-synth: delegating to LLM synthesis",
    )


# ── story-enrichment-synth ────────────────────────────────────────────────────

@register("story-enrichment-synth")
def handle_story_enrichment_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(ctx.workflow_dir, "stories.md", "design-seed.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Enrich stories.md for slug '{ctx.slug}' WITHOUT changing scope. "
            f"Existing artifacts: {existing}. "
            "Add: explicit edge cases, validation requirements, error handling, "
            "cross-cutting concerns (auth/logging/observability), integration test hints."
        ),
        message="story-enrichment-synth: delegating to LLM synthesis",
    )


# ── openspec-synth ────────────────────────────────────────────────────────────

@register("openspec-synth")
def handle_openspec_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(
        ctx.workflow_dir, "stories.md", "design-seed.md", "execution-path.json"
    )
    # Pull interface signal count if available
    ep_json = ctx.workflow_dir / "execution-path.json"
    interface_count = 0
    if ep_json.exists():
        try:
            data = json.loads(ep_json.read_text(encoding="utf-8"))
            interface_count = data.get("features", {}).get("interface_signals", 0)
        except Exception:
            pass

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Generate openspec.md (interface contracts) for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. Interface signals detected: {interface_count}. "
            f"Context hint: {ctx.reason or 'none provided'}. "
            "Document: API endpoints/functions (method, input schema, output schema, errors), "
            "data models, events/messages, auth/permissions."
        ),
        message=f"openspec-synth: delegating to LLM synthesis ({interface_count} interface signals)",
    )


# ── openspec-sync ─────────────────────────────────────────────────────────────

@register("openspec-sync")
def handle_openspec_sync(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Reconcile an existing openspec against current stories without full regen."""
    existing = _artifact_hint(ctx.workflow_dir, "openspec.md", "stories.md")
    if not (ctx.workflow_dir / "openspec.md").exists():
        return CommandResult(
            state=state,
            ok=False,
            needs_llm=False,
            message="openspec-sync: no openspec.md found — run openspec-synth first",
            warnings=["openspec.md does not exist"],
        )
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Sync openspec.md against current stories.md for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            "Identify: new interfaces not yet in spec, removed interfaces, changed contracts. "
            "Produce a diff-style update section appended to openspec.md."
        ),
        message="openspec-sync: delegating to LLM synthesis",
    )


# ── implementation-plan-synth ─────────────────────────────────────────────────

@register("implementation-plan-synth")
def handle_implementation_plan_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(
        ctx.workflow_dir,
        "stories.md", "openspec.md", "design-seed.md",
        "execution-path.json", "implementation-plan.md",
    )
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Generate implementation-plan.md for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"Context hint: {ctx.reason or 'none provided'}. "
            "Produce: ordered build sequence with rationale, parallel tracks if applicable, "
            "integration checkpoints, risk items, definition of done "
            "(all ACs green, drift score ≥ 0.60, no unresolved HIGH CI failures, "
            "all blocking verdicts recorded)."
        ),
        message="implementation-plan-synth: delegating to LLM synthesis",
    )


# ── feedback-synth ────────────────────────────────────────────────────────────

@register("feedback-synth")
def handle_feedback_synth(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(
        ctx.workflow_dir,
        "review-log.md", "ci-feedback.md", "drift-check.md",
    )
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Synthesise team feedback for slug '{ctx.slug}' at stage "
            f"'{state.current_stage.value}'. "
            f"Existing artifacts: {existing}. "
            f"Additional context: {ctx.reason or 'none provided'}. "
            "Produce feedback-synthesis.md: blocking items (role-attributed), "
            "non-blocking notes, deferred items, recommended next shapeitup command."
        ),
        message="feedback-synth: delegating to LLM synthesis",
    )


# ── issue-advisor ─────────────────────────────────────────────────────────────

@register("issue-advisor")
def handle_issue_advisor(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    if not ctx.reason.strip():
        return CommandResult(
            state=state,
            ok=False,
            message="issue-advisor: provide the blocking issue as --reason",
            warnings=["No issue description provided"],
        )
    existing = _artifact_hint(
        ctx.workflow_dir,
        "ci-feedback.md", "review-log.md", "implementation-plan.md",
    )
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Advise on blocking issue for slug '{ctx.slug}' at stage "
            f"'{state.current_stage.value}'. "
            f"Issue: {ctx.reason}. "
            f"Existing context artifacts: {existing}. "
            "Output directly to user: issue restatement, root cause hypothesis, "
            "three options (approach / trade-off / effort S|M|L), "
            "recommended option with rationale, suggested shapeitup command."
        ),
        message=f"issue-advisor: delegating to LLM — issue: {ctx.reason[:60]}",
    )


# ── replan ────────────────────────────────────────────────────────────────────

@register("replan")
def handle_replan(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    existing = _artifact_hint(
        ctx.workflow_dir, "stories.md", "implementation-plan.md"
    )
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Replan implementation for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"New constraints/changes: {ctx.reason or 'none provided'}. "
            "Append a '## Replan — <date>' section to implementation-plan.md: "
            "what changed, updated build order (changed sections only), "
            "new/removed risk items."
        ),
        message="replan: delegating to LLM synthesis",
    )


# ── verify-fix ────────────────────────────────────────────────────────────────

@register("verify-fix")
def handle_verify_fix(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    if not ctx.reason.strip():
        return CommandResult(
            state=state,
            ok=False,
            message="verify-fix: describe the fix as --reason",
            warnings=["No fix description provided"],
        )
    existing = _artifact_hint(ctx.workflow_dir, "ci-feedback.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Verify fix for slug '{ctx.slug}'. "
            f"Fix description: {ctx.reason}. "
            f"Existing CI context: {existing}. "
            "Output to user: failure class addressed, does fix target root cause, "
            "new risks introduced, suggested test to confirm fix, "
            "whether to re-run shapeitup:ci-feedback with updated log."
        ),
        message=f"verify-fix: delegating to LLM — fix: {ctx.reason[:60]}",
    )


# ── staff ─────────────────────────────────────────────────────────────────────

@register("staff")
def handle_staff(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Suggest role assignments for stories. LLM reads stories and recommends
    which team roles should lead each story.
    """
    existing = _artifact_hint(ctx.workflow_dir, "stories.md", "capabilities.md")
    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"Suggest staffing for slug '{ctx.slug}'. "
            f"Existing artifacts: {existing}. "
            f"Additional context: {ctx.reason or 'none'}. "
            "For each story, recommend which active team role should lead it "
            "(Tech Lead, Implementer, QA Engineer) with a one-line rationale. "
            "Output a staffing-plan.md."
        ),
        message="staff: delegating to LLM synthesis",
    )


# ── assign ────────────────────────────────────────────────────────────────────

@register("assign")
def handle_assign(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Record a story assignment. ctx.role = role, ctx.reason = story name.
    """
    if not ctx.reason.strip():
        return CommandResult(
            state=state,
            ok=False,
            message="assign: provide story name as --reason and role as --role",
            warnings=["No story or role specified"],
        )

    entry = {
        "story": ctx.reason,
        "role": ctx.role or "unspecified",
        "stage": state.current_stage.value,
        "timestamp": _now(),
    }
    log_path = ctx.workflow_dir / "assignments.jsonl"
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    state._touch()
    return CommandResult(
        state=state,
        message=f"assign: {ctx.reason[:50]} → {ctx.role or 'unspecified'}",
        artifacts_written=["assignments.jsonl"],
    )
