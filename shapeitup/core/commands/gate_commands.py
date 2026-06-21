"""
gate_commands.py
----------------
Handlers for gate navigation commands:
  approve, reject, next, override, reconcile, refine, rework-item,
  proceed-only, defer, actions
"""
from __future__ import annotations

from shapeitup.core.commands._base import CommandContext, CommandResult
from shapeitup.core.commands._registry import register
from shapeitup.core.state import WorkflowState, Stage, GateStatus, GATED_STAGES
from shapeitup.core.transitions import allowed_commands_for
from shapeitup.core.team import ActiveTeam, Verdict


# ── approve ────────────────────────────────────────────────────────────────────

@register("approve")
def handle_approve(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    prev_stage = state.current_stage
    state.apply_approve(ctx.reason)
    return CommandResult(
        state=state,
        message=(
            f"approve: {prev_stage.value} → {state.current_stage.value} "
            f"| gate={state.gate_status.value} "
            f"| next={state.next_action}"
        ),
    )


# ── reject / rework ────────────────────────────────────────────────────────────

@register("reject", "rework")
def handle_reject(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    prev_stage = state.current_stage
    state.apply_reject(ctx.reason)
    return CommandResult(
        state=state,
        message=(
            f"reject: {prev_stage.value} → {state.current_stage.value} "
            f"| reason={ctx.reason or '(none)'}"
        ),
    )


# ── next ───────────────────────────────────────────────────────────────────────

@register("next")
def handle_next(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    if state.is_gated and state.gate_status != GateStatus.APPROVED:
        return CommandResult(
            state=state,
            message=(
                f"next: gate still pending at '{state.current_stage.value}'. "
                f"Use approve or reject to advance."
            ),
            warnings=["Gate is pending — use approve or reject"],
        )
    prev = state.current_stage
    state.apply_next()
    return CommandResult(
        state=state,
        message=f"next: {prev.value} → {state.current_stage.value}",
    )


# ── override ───────────────────────────────────────────────────────────────────

@register("override")
def handle_override(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    # Override clears any block and records the waiver reason
    if state.gate_status == GateStatus.BLOCKED:
        state.gate_status = GateStatus.PENDING
        state.blocked_reason = ""
    state.item_note = f"Override applied: {ctx.reason}" if ctx.reason else "Override applied"
    state._touch()
    return CommandResult(
        state=state,
        message=f"override: gate cleared | reason={ctx.reason or '(none)'}",
    )


# ── reconcile ──────────────────────────────────────────────────────────────────

@register("reconcile")
def handle_reconcile(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    state.gate_status = GateStatus.PENDING
    state.next_action = (
        f"Reconcile workflow artifacts with repository state. {ctx.reason}"
        if ctx.reason else
        "Reconcile workflow artifacts with repository state before continuing."
    )
    state.item_note = "Reconciliation requested"
    state._touch()
    return CommandResult(
        state=state,
        message=f"reconcile: staying at '{state.current_stage.value}' | {state.next_action}",
    )


# ── refine ─────────────────────────────────────────────────────────────────────

@register("refine")
def handle_refine(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    state.next_action = f"Refine: {ctx.reason}" if ctx.reason else state.next_action
    state.item_note = f"Refinement requested: {ctx.reason}"
    state._touch()
    return CommandResult(
        state=state,
        message=f"refine: staying at '{state.current_stage.value}' | {ctx.reason or '(none)'}",
    )


# ── rework-item ────────────────────────────────────────────────────────────────

@register("rework-item")
def handle_rework_item(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    items = ctx.items or ctx.reason
    state.gate_status = GateStatus.PENDING
    state.item_note = f"Rework requested for: {items}"
    state.next_action = f"Rework item(s): {items}"
    state._touch()
    return CommandResult(
        state=state,
        message=f"rework-item: '{items}' marked for rework at '{state.current_stage.value}'",
    )


# ── proceed-only ───────────────────────────────────────────────────────────────

@register("proceed-only")
def handle_proceed_only(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    items = ctx.items or ctx.reason
    state.active_items = items
    state.item_note = f"Scope restricted to: {items}"
    state.next_action = f"Proceed with: {items} — other items deferred"
    state._touch()
    return CommandResult(
        state=state,
        message=f"proceed-only: scope restricted to '{items}'",
    )


# ── defer ──────────────────────────────────────────────────────────────────────

@register("defer")
def handle_defer(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    items = ctx.items or ctx.reason
    existing = state.deferred_items
    state.deferred_items = f"{existing}, {items}".lstrip(", ") if existing else items
    state.item_note = f"Deferred: {items}"
    state._touch()
    return CommandResult(
        state=state,
        message=f"defer: '{items}' deferred",
    )


# ── actions ────────────────────────────────────────────────────────────────────

@register("actions")
def handle_actions(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    from shapeitup.core.transitions import ALWAYS_ALLOWED
    stage_cmds = sorted(allowed_commands_for(state.current_stage) - ALWAYS_ALLOWED - {"override"})
    universal = sorted(ALWAYS_ALLOWED)

    lines = [
        f"# Actions — {state.current_stage.value}",
        f"Gate status: {state.gate_status.value}",
        "",
        "## Stage-specific commands",
        *[f"  shapeitup:{c}" for c in stage_cmds],
        "",
        "## Always available",
        *[f"  shapeitup:{c}" for c in universal],
        "  shapeitup:override",
    ]

    if team:
        gate = team.check_gate()
        lines += ["", "## Team gate", gate.error_message() if not gate.can_advance else "✓ All blocking roles approved"]

    action_menu = "\n".join(lines)
    menu_path = ctx.workflow_dir / "action-menu.md"
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    menu_path.write_text(action_menu, encoding="utf-8")

    return CommandResult(
        state=state,
        message=action_menu,
        artifacts_written=["action-menu.md"],
    )
