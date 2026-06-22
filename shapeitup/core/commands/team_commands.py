"""
team_commands.py
----------------
Handlers for team-related commands:
  team-verdict, challenge, review-sync, memory-record, debt-record, accounting-record
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from shapeitup.core.commands._base import CommandContext, CommandResult
from shapeitup.core.commands._registry import register
from shapeitup.core.state import WorkflowState
from shapeitup.core.team import ActiveTeam, Verdict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── team-verdict ───────────────────────────────────────────────────────────────

@register("team-verdict")
def handle_team_verdict(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Record a role verdict programmatically.
    ctx.role: role name (e.g. "product-owner")
    ctx.verdict: "approve" | "approve-with-changes" | "block"
    ctx.findings: pipe-separated blocking findings
    ctx.reason: summary
    """
    if team is None:
        return CommandResult(
            state=state,
            message="team-verdict: no active team for this story",
            warnings=["Team not initialised — run shapeitup:discuss first"],
        )

    try:
        verdict = Verdict(ctx.verdict or "approve")
    except ValueError:
        verdict = Verdict.APPROVE

    findings = [f.strip() for f in ctx.findings.split("|") if f.strip()]

    team.record_verdict(
        role_name=ctx.role,
        verdict=verdict,
        summary=ctx.reason,
        blocking_findings=findings,
    )

    gate = team.check_gate()
    if not gate.can_advance and verdict == Verdict.BLOCK:
        state.apply_block(f"{ctx.role} blocked: {findings[0] if findings else ctx.reason}")

    return CommandResult(
        state=state,
        message=(
            f"team-verdict: {ctx.role} → {verdict.value} "
            f"| gate={'✓' if gate.can_advance else '✗ ' + gate.reason}"
        ),
        ml_outputs={"gate_can_advance": gate.can_advance},
    )


# ── challenge ──────────────────────────────────────────────────────────────────

@register("challenge")
def handle_challenge(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    entry = {
        "role": ctx.role or "unspecified",
        "finding": ctx.reason,
        "stage": state.current_stage.value,
        "timestamp": _now(),
    }
    log_path = ctx.workflow_dir / "review-log.md"
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    log_path.write_text(
        existing + f"\n## Challenge — {entry['role']} @ {entry['stage']}\n{entry['finding']}\n",
        encoding="utf-8",
    )
    state.challenge_note = f"{ctx.role}: {ctx.reason}"
    state._touch()
    return CommandResult(
        state=state,
        message=f"challenge: recorded from {ctx.role or 'unspecified'}",
        artifacts_written=["review-log.md"],
    )


# ── review-sync ────────────────────────────────────────────────────────────────

_SECURITY_HIGH_RE = re.compile(r"\b(?:severity|finding)[:\s]+high\b", re.IGNORECASE)


def _scan_security_artifact(workflow_dir, stage: str) -> list[str]:
    """Return list of HIGH-severity lines from the security review artifact, if any."""
    artifact = workflow_dir / f"reviews/security-review-{stage}.md"
    if not artifact.exists():
        return []
    findings: list[str] = []
    for line in artifact.read_text(encoding="utf-8").splitlines():
        if _SECURITY_HIGH_RE.search(line):
            findings.append(line.strip())
    return findings


@register("review-sync")
def handle_review_sync(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Run the gate check and return structured ml_outputs so the skill can decide
    whether to auto-advance, pause for human input, or surface blocking findings.

    ml_outputs schema:
      gate_clear: bool
      stop_reason: null | "pending_reviews" | "role_block" | "security_high"
      pending_roles: list[str]
      blocking_roles: list[str]
      security_high_findings: list[str]   # non-empty only when stop_reason == "security_high"
      auto_advance: bool  # True when gate_clear AND no security/block issues
    """
    if team is None:
        return CommandResult(
            state=state,
            message="review-sync: no active team",
            ml_outputs={
                "gate_clear": False,
                "stop_reason": "no_team",
                "pending_roles": [],
                "blocking_roles": [],
                "security_high_findings": [],
                "auto_advance": False,
            },
        )

    gate = team.check_gate(
        workflow_dir=ctx.workflow_dir,
        stage=state.current_stage.value,
    )
    stage = state.current_stage.value

    # Scan security artifact for HIGH findings regardless of gate status
    security_highs = _scan_security_artifact(ctx.workflow_dir, stage)

    if gate.can_advance:
        state.challenge_note = ""
        # Even if gate passes, HIGH security findings must stop auto-advance
        if security_highs:
            stop_reason: str | None = "security_high"
            auto_advance = False
            state.next_action = "Security HIGH finding — requires human review before advancing"
        else:
            stop_reason = None
            auto_advance = True
            state.next_action = "Gate cleared — auto-advancing stage"
    else:
        auto_advance = False
        if gate.blocking_roles:
            stop_reason = "role_block"
            state.next_action = gate.error_message()
        else:
            stop_reason = "pending_reviews"
            state.next_action = gate.error_message()

    state._touch()

    msg = (
        f"review-sync: gate={'✓ clear' if gate.can_advance else '✗ ' + gate.reason}"
        + (f" | security HIGH findings: {len(security_highs)}" if security_highs else "")
        + (f" | auto_advance: {auto_advance}")
    )

    return CommandResult(
        state=state,
        message=msg,
        ml_outputs={
            "gate_clear": gate.can_advance,
            "stop_reason": stop_reason,
            "pending_roles": gate.pending_roles,
            "blocking_roles": gate.blocking_roles,
            "security_high_findings": security_highs,
            "auto_advance": auto_advance,
        },
    )


# ── memory-record ──────────────────────────────────────────────────────────────

@register("memory-record")
def handle_memory_record(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": _now(), "note": ctx.reason, "stage": state.current_stage.value}
    _append_jsonl(ctx.workflow_dir / "records" / "memory.jsonl", record)
    _refresh_md(
        ctx.workflow_dir / "memory.md",
        f"## Memory — {_now()[:10]}\n{ctx.reason}\n",
    )
    return CommandResult(
        state=state,
        message=f"memory-record: saved",
        artifacts_written=["records/memory.jsonl", "memory.md"],
    )


# ── debt-record ────────────────────────────────────────────────────────────────

@register("debt-record")
def handle_debt_record(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": _now(), "debt": ctx.reason, "stage": state.current_stage.value}
    _append_jsonl(ctx.workflow_dir / "records" / "debt.jsonl", record)
    _refresh_md(
        ctx.workflow_dir / "debt.md",
        f"## Debt — {_now()[:10]}\n{ctx.reason}\n",
    )
    return CommandResult(
        state=state,
        message="debt-record: saved",
        artifacts_written=["records/debt.jsonl", "debt.md"],
    )


# ── accounting-record ──────────────────────────────────────────────────────────

@register("accounting-record")
def handle_accounting_record(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": _now(), "entry": ctx.reason, "stage": state.current_stage.value}
    _append_jsonl(ctx.workflow_dir / "records" / "invocations.jsonl", record)
    return CommandResult(
        state=state,
        message="accounting-record: saved",
        artifacts_written=["records/invocations.jsonl"],
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _append_jsonl(path: "Path", record: dict) -> None:
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _refresh_md(path: "Path", content: str) -> None:
    from pathlib import Path
    path = Path(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + content, encoding="utf-8")
