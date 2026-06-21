"""
cli.py
------
Main CLI dispatcher for shapeitup.

Pipeline for every command:
  1. Load state (JSON preferred, MD fallback)
  2. Assemble team from ML signals (path_classifier — no LLM)
  3. check_full_gate() — transition table + team verdict check
  4. Dispatch to command handler
  5. Save state atomically
  6. Postprocess: history, context ranking
  7. Print result

LLM is never called from this dispatcher. Synthesis commands set
result.needs_llm=True and result.llm_task — the skill reads these
and invokes the LLM only then.

Usage:
  shapeitup --slug <slug> --root <path> --command <cmd> [--reason "..."]
            [--items "..."] [--role "..."] [--verdict "..."] [--findings "..."]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _ensure_commands_registered() -> None:
    """Import all command modules to trigger @register() decorators."""
    from shapeitup.core.commands import gate_commands  # noqa: F401
    from shapeitup.core.commands import team_commands   # noqa: F401
    from shapeitup.core.commands import analysis_commands  # noqa: F401


def _load_team(state: "WorkflowState", workflow_dir: Path) -> "ActiveTeam":
    """
    Load or rebuild the active team from ML signals.
    If story_signals are cached in state, use them.
    Otherwise assemble from stories.md if it exists, else use blank signals.
    Restores persisted verdicts from state.team_verdicts.
    """
    from shapeitup.core.team import assemble_team, assemble_team_from_signals, StorySignals, RoleVerdict, Verdict

    if state.story_signals:
        signals = StorySignals(
            flagged=bool(state.story_signals.get("flagged", False)),
            security_signal=bool(state.story_signals.get("security_signal", False)),
            interface_signals=int(state.story_signals.get("interface_signals", 0)),
            multi_service_signal=bool(state.story_signals.get("multi_service_signal", False)),
        )
        team = assemble_team_from_signals(signals)
    else:
        stories_path = workflow_dir / "stories.md"
        if stories_path.exists():
            team = assemble_team(stories_path.read_text(encoding="utf-8")[:4000])
        else:
            team = assemble_team_from_signals(StorySignals())

    # Restore persisted verdicts so gate checks survive across CLI invocations
    for role_name, vdata in state.team_verdicts.items():
        try:
            verdict_val = Verdict(vdata.get("verdict", "pending"))
        except ValueError:
            verdict_val = Verdict.PENDING
        team.verdicts[role_name] = RoleVerdict(
            role_name=role_name,
            verdict=verdict_val,
            summary=str(vdata.get("summary", "")),
            blocking_findings=list(vdata.get("blocking_findings", [])),
            changes_requested=list(vdata.get("changes_requested", [])),
        )

    return team


def _append_history(workflow_dir: Path, command: str, state: "WorkflowState", message: str) -> None:
    history_path = workflow_dir / "history.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"\n## {ts} — {command}\n"
        f"Stage: {state.current_stage.value} | Gate: {state.gate_status.value}\n"
        f"{message}\n"
    )
    existing = history_path.read_text(encoding="utf-8") if history_path.exists() else "# History\n"
    history_path.write_text(existing + entry, encoding="utf-8")


def run(
    slug: str,
    root: Path,
    command: str,
    reason: str = "",
    items: str = "",
    role: str = "",
    verdict: str = "",
    findings: str = "",
    design_file: str = "",
    output_format: str = "text",
) -> dict:
    """
    Core dispatcher. Returns a result dict — usable programmatically or from CLI.
    """
    from shapeitup.core.state import WorkflowState
    from shapeitup.core.transitions import check_full_gate, ALWAYS_ALLOWED
    from shapeitup.core.commands._base import CommandContext
    from shapeitup.core.commands._registry import lookup

    _ensure_commands_registered()

    workflow_dir = root / ".workflow" / slug
    workflow_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Load state ──────────────────────────────────────────────────────────
    state = WorkflowState.load(workflow_dir)
    if not state.slug:
        state.slug = slug

    # ── 2. Assemble team from ML signals ───────────────────────────────────────
    team = _load_team(state, workflow_dir)

    # ── 3. Gate check ──────────────────────────────────────────────────────────
    # override bypasses both checks; always_allowed bypass team gate
    if command != "override":
        gate_result = check_full_gate(
            stage=state.current_stage,
            command=command,
            team=team if command in {"approve", "next"} else None,
        )
        if not gate_result.can_proceed:
            error_msg = gate_result.error_message()
            if output_format == "json":
                return {"ok": False, "error": error_msg, "command": command,
                        "stage": state.current_stage.value}
            print(f"ERROR: {error_msg}", file=sys.stderr)
            sys.exit(1)

    # ── 4. Dispatch ────────────────────────────────────────────────────────────
    ctx = CommandContext(
        slug=slug, root=root, reason=reason, items=items,
        role=role, verdict=verdict, findings=findings, design_file=design_file,
    )

    try:
        handler = lookup(command)
    except KeyError as exc:
        if output_format == "json":
            return {"ok": False, "error": str(exc), "command": command}
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    cmd_result = handler(state, ctx, team)

    # ── 5. Save state ──────────────────────────────────────────────────────────
    # Sync team verdicts back to state so they persist across CLI calls
    if team is not None:
        cmd_result.state.team_verdicts = {
            role_name: {
                "verdict": rv.verdict.value,
                "summary": rv.summary,
                "blocking_findings": rv.blocking_findings,
                "changes_requested": rv.changes_requested,
            }
            for role_name, rv in team.verdicts.items()
        }
    cmd_result.state.save(workflow_dir)

    # ── 6. Postprocess ─────────────────────────────────────────────────────────
    _append_history(workflow_dir, command, cmd_result.state, cmd_result.message)

    # ── 7. Build return value ──────────────────────────────────────────────────
    output = {
        "ok": True,
        "command": command,
        "slug": slug,
        "stage": cmd_result.state.current_stage.value,
        "gate_status": cmd_result.state.gate_status.value,
        "next_action": cmd_result.state.next_action,
        "message": cmd_result.message,
        "needs_llm": cmd_result.needs_llm,
        "llm_task": cmd_result.llm_task,
        "ml_outputs": cmd_result.ml_outputs,
        "artifacts_written": cmd_result.artifacts_written,
        "warnings": cmd_result.warnings,
        "team": {
            "active_roles": team.active_role_names,
            "gate_can_advance": team.check_gate().can_advance,
        },
    }
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="shapeitup — ML-powered staged delivery workflow engine"
    )
    parser.add_argument("--slug",    required=True,  help="Workflow slug (e.g. my-feature)")
    parser.add_argument("--root",    default=".",     help="Repo root directory")
    parser.add_argument("--command", required=True,   help="Workflow command")
    parser.add_argument("--reason",  default="",      help="Reason / log text / objective")
    parser.add_argument("--items",   default="",      help="Named items for proceed-only/defer")
    parser.add_argument("--role",    default="",      help="Role name for team-verdict/challenge")
    parser.add_argument("--verdict", default="",      help="Verdict for team-verdict")
    parser.add_argument("--findings",default="",      help="Pipe-separated blocking findings")
    parser.add_argument("--design-file", default="",  help="Path to design file")
    parser.add_argument("--json",    action="store_true", help="Output JSON instead of text")
    args = parser.parse_args(argv)

    result = run(
        slug=args.slug,
        root=Path(args.root).resolve(),
        command=args.command,
        reason=args.reason,
        items=args.items,
        role=args.role,
        verdict=args.verdict,
        findings=args.findings,
        design_file=args.design_file,
        output_format="json" if args.json else "text",
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("ok"):
            print(result["message"])
            if result.get("warnings"):
                for w in result["warnings"]:
                    print(f"  ⚠ {w}", file=sys.stderr)
            if result.get("needs_llm"):
                print(f"\n[LLM task] {result['llm_task']}")
        else:
            print(f"ERROR: {result.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
