"""
analysis_commands.py
--------------------
Handlers for ML-powered analysis commands — these run locally
without any LLM call:
  ci-feedback       → failure_classifier
  execution-path    → path_classifier
  drift-check       → drift_detector
  dag-sync          → graph algorithms only
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


# ── ci-feedback ────────────────────────────────────────────────────────────────

@register("ci-feedback")
def handle_ci_feedback(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Classify CI failure text using failure_classifier — no LLM needed.
    ctx.reason: raw CI failure log text
    """
    from shapeitup.ml.failure_classifier import classify_failure

    if not ctx.reason.strip():
        return CommandResult(
            state=state,
            message="ci-feedback: no failure text provided",
            warnings=["Provide failure log text as --reason"],
        )

    result = classify_failure(ctx.reason)

    output = {
        "failure_class": result.failure_class,
        "confidence": round(result.confidence, 3),
        "method": result.method,
        "is_retryable": result.is_retryable,
        "severity": result.severity,
        "matched_pattern": result.matched_pattern,
        "timestamp": _now(),
        "stage": state.current_stage.value,
    }

    # Write structured output
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    ci_json = ctx.workflow_dir / "ci-feedback.json"
    ci_md = ctx.workflow_dir / "ci-feedback.md"
    ci_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    ci_md.write_text(_ci_feedback_md(output, ctx.reason), encoding="utf-8")

    # Block if non-retryable high-severity failure
    if result.severity == "high" and not result.is_retryable:
        state.apply_block(f"CI failure: {result.failure_class} (confidence={result.confidence:.0%})")

    return CommandResult(
        state=state,
        message=(
            f"ci-feedback: {result.failure_class} | "
            f"severity={result.severity} | "
            f"retryable={result.is_retryable} | "
            f"confidence={result.confidence:.0%}"
        ),
        ml_outputs=output,
        artifacts_written=["ci-feedback.json", "ci-feedback.md"],
    )


def _ci_feedback_md(output: dict, raw_log: str) -> str:
    return (
        f"# CI Feedback\n\n"
        f"- Failure class: `{output['failure_class']}`\n"
        f"- Severity: `{output['severity']}`\n"
        f"- Retryable: `{output['is_retryable']}`\n"
        f"- Confidence: `{output['confidence']:.0%}` ({output['method']})\n"
        f"- Timestamp: {output['timestamp']}\n\n"
        f"## Raw log\n```\n{raw_log[:2000]}\n```\n"
    )


# ── execution-path ─────────────────────────────────────────────────────────────

@register("execution-path")
def handle_execution_path(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Classify active story as simple/flagged using path_classifier — no LLM.
    ctx.reason: story text (or path to stories.md if prefixed with @)
    """
    from shapeitup.ml.path_classifier import classify_path
    from shapeitup.core.team import StorySignals

    story_text = _resolve_story_text(ctx)
    if not story_text.strip():
        return CommandResult(
            state=state,
            message="execution-path: no story text provided",
            warnings=["Provide story text via --reason or point to stories.md"],
        )

    result = classify_path(story_text)
    signals = StorySignals.from_path_result(result)

    output = {
        "path_type": result.path_type,
        "confidence": round(result.confidence, 3),
        "rationale": result.rationale,
        "features": {
            "word_count": result.features.word_count,
            "ac_count": result.features.ac_count,
            "dep_count": result.features.dep_count,
            "interface_signals": result.features.interface_signals,
            "security_signal": result.features.security_signal,
            "multi_service_signal": result.features.multi_service_signal,
            "complexity_score": round(result.features.complexity_score, 2),
        },
        "timestamp": _now(),
    }

    # Store signals in state for team assembly
    state.story_signals = {
        "flagged": signals.flagged,
        "security_signal": signals.security_signal,
        "interface_signals": signals.interface_signals,
        "multi_service_signal": signals.multi_service_signal,
    }
    state._touch()

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    (ctx.workflow_dir / "execution-path.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    (ctx.workflow_dir / "execution-path.md").write_text(
        _execution_path_md(output), encoding="utf-8"
    )

    return CommandResult(
        state=state,
        message=(
            f"execution-path: {result.path_type} | "
            f"complexity={result.features.complexity_score:.1f} | "
            f"confidence={result.confidence:.0%}"
        ),
        ml_outputs=output,
        artifacts_written=["execution-path.json", "execution-path.md"],
    )


def _resolve_story_text(ctx: CommandContext) -> str:
    """If reason starts with @, treat remainder as a file path."""
    if ctx.reason.startswith("@"):
        p = Path(ctx.reason[1:])
        if not p.is_absolute():
            p = ctx.root / p
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""
    return ctx.reason


def _execution_path_md(output: dict) -> str:
    f = output["features"]
    return (
        f"# Execution Path\n\n"
        f"- Path type: `{output['path_type']}`\n"
        f"- Confidence: `{output['confidence']:.0%}`\n"
        f"- Rationale: {output['rationale']}\n\n"
        f"## Features\n"
        f"- Words: {f['word_count']} | ACs: {f['ac_count']} | "
        f"Deps: {f['dep_count']} | Interfaces: {f['interface_signals']}\n"
        f"- Security signal: {f['security_signal']} | "
        f"Multi-service: {f['multi_service_signal']}\n"
        f"- Complexity score: {f['complexity_score']}\n"
    )


# ── drift-check ────────────────────────────────────────────────────────────────

@register("drift-check")
def handle_drift_check(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Detect design↔code drift using embedding similarity — no LLM.
    Reads capabilities.md + design-seed.md as design text.
    Scans src/ for code snippets.
    """
    from shapeitup.ml.drift_detector import DriftDetector

    wf = ctx.workflow_dir
    design_parts: list[str] = []
    for fname in ("capabilities.md", "design-seed.md", "stories.md"):
        p = wf / fname
        if p.exists():
            design_parts.append(p.read_text(encoding="utf-8"))

    if not design_parts:
        return CommandResult(
            state=state,
            message="drift-check: no design artifacts found in workflow dir",
            warnings=["Run shapeitup:discuss first to create capabilities.md"],
        )

    # Gather code snippets (first 3000 chars of each source file)
    code_snippets: list[str] = []
    for pattern in ("**/*.py", "**/*.ts", "**/*.js", "**/*.java", "**/*.go"):
        for p in list(ctx.root.glob(pattern))[:10]:
            if ".workflow" in str(p) or "node_modules" in str(p):
                continue
            try:
                code_snippets.append(p.read_text(encoding="utf-8")[:3000])
            except Exception:
                pass

    detector = DriftDetector()
    result = detector.detect(
        design_text="\n\n".join(design_parts),
        code_snippets=code_snippets,
    )

    output = {
        "drift_type": result.drift_type,
        "score": round(result.score, 3),
        "explanation": result.explanation,
        "needs_reconciliation": result.needs_reconciliation,
        "severity": result.severity,
        "design_terms": result.design_terms[:6],
        "code_terms": result.code_terms[:6],
        "timestamp": _now(),
    }

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    (wf / "drift-check.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    (wf / "drift-check.md").write_text(
        f"# Drift Check\n\n- Type: `{result.drift_type}`\n"
        f"- Score: `{result.score:.3f}` (1.0 = identical)\n"
        f"- Severity: `{result.severity}`\n\n"
        f"## Explanation\n{result.explanation}\n",
        encoding="utf-8",
    )

    if result.needs_reconciliation:
        state.next_action = f"Drift detected ({result.drift_type}) — consider shapeitup:reconcile"
        state._touch()

    return CommandResult(
        state=state,
        message=f"drift-check: {result.drift_type} | score={result.score:.3f} | {result.explanation[:80]}",
        ml_outputs=output,
        artifacts_written=["drift-check.json", "drift-check.md"],
    )


# ── dag-sync ───────────────────────────────────────────────────────────────────

@register("dag-sync")
def handle_dag_sync(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Build story DAG from Depends-on declarations in stories.md.
    Pure graph algorithm — no LLM call.
    """
    stories_path = ctx.workflow_dir / "stories.md"
    if not stories_path.exists():
        return CommandResult(
            state=state,
            message="dag-sync: stories.md not found",
            warnings=["Run story-synth first"],
        )

    dag, errors = _parse_dag(stories_path.read_text(encoding="utf-8"))
    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)

    dag_output = {
        "nodes": list(dag.keys()),
        "edges": dag,
        "errors": errors,
        "timestamp": _now(),
    }
    (ctx.workflow_dir / "dag.json").write_text(json.dumps(dag_output, indent=2), encoding="utf-8")
    (ctx.workflow_dir / "dag-validation.md").write_text(
        _dag_validation_md(dag, errors), encoding="utf-8"
    )

    return CommandResult(
        state=state,
        message=f"dag-sync: {len(dag)} stories | {len(errors)} validation issues",
        ml_outputs=dag_output,
        artifacts_written=["dag.json", "dag-validation.md"],
    )


def _parse_dag(stories_text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Parse story names and Depends-on lines from stories.md."""
    import re
    dag: dict[str, list[str]] = {}
    errors: list[str] = []
    current: str | None = None

    for line in stories_text.splitlines():
        # Story heading: ## Story N or ## Story: Name
        m = re.match(r"^##\s+(.+)", line)
        if m:
            current = m.group(1).strip()
            dag[current] = []
            continue
        # Depends on line
        m = re.match(r"^[- ]*[Dd]epends?\s+on\s*:\s*(.+)", line)
        if m and current:
            deps = [d.strip() for d in m.group(1).split(",") if d.strip()]
            dag[current].extend(deps)

    # Validate: check all declared deps exist
    known = set(dag.keys())
    for story, deps in dag.items():
        for dep in deps:
            if dep not in known:
                errors.append(f"'{story}' depends on unknown story '{dep}'")

    # Detect cycles (simple DFS)
    visited: set[str] = set()
    path: set[str] = set()

    def has_cycle(node: str) -> bool:
        if node in path:
            errors.append(f"Cycle detected involving '{node}'")
            return True
        if node in visited:
            return False
        visited.add(node)
        path.add(node)
        for dep in dag.get(node, []):
            if has_cycle(dep):
                return True
        path.discard(node)
        return False

    for story in dag:
        has_cycle(story)

    return dag, errors


def _dag_validation_md(dag: dict, errors: list[str]) -> str:
    lines = [f"# DAG Validation\n\n- Stories: {len(dag)}\n- Issues: {len(errors)}\n"]
    if errors:
        lines.append("\n## Issues\n" + "\n".join(f"- {e}" for e in errors))
    else:
        lines.append("\n✓ No issues found.")
    return "\n".join(lines)
