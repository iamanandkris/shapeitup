"""
agent_commands.py
-----------------
Active agent commands — each role does real work, not just votes.

Mechanical commands (Python-only):
  stage-plan      → compute phase execution plan for current stage
  impl-schedule   → DAG-driven story implementation schedule

LLM-delegating commands (return needs_llm=True with precise llm_task):
  po-review       → Product Owner reviews stage artifacts
  tl-review       → Tech Lead reviews technical approach
  qa-review       → QA Engineer reviews testability
  security-scan   → Security Reviewer scans for vulnerabilities
  qa-test-spec    → QA writes failing test specifications (TDD step 1)
  pair-propose    → Implementer A proposes implementation
  pair-challenge  → Implementer B challenges proposal
  pair-implement  → Write final consensus implementation
  tl-impl-review  → Tech Lead reviews implemented code
  qa-validate     → QA validates tests pass post-implementation
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


def _slug(text: str) -> str:
    """Slugify for artifact naming."""
    return text.lower().replace(" ", "-").replace(":", "").replace("/", "-")[:40]


def _read_artifact(workflow_dir: Path, *names: str) -> str:
    """Read first existing artifact, return empty string if none found."""
    for name in names:
        p = workflow_dir / name
        if p.exists():
            return p.read_text(encoding="utf-8")[:6000]
    return ""


# ── stage-plan ────────────────────────────────────────────────────────────────

@register("stage-plan")
def handle_stage_plan(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Compute the phase execution plan for the current stage.
    Returns JSON plan that the skill uses to spawn parallel agents.
    """
    from shapeitup.core.stage_planner import compute_stage_plan

    if team is None:
        return CommandResult(
            state=state,
            ok=False,
            message="stage-plan: no active team — run execution-path first",
            warnings=["Team not assembled"],
        )

    plan = compute_stage_plan(
        stage=state.current_stage,
        team=team,
        workflow_dir=ctx.workflow_dir,
    )
    plan_dict = plan.to_dict()

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    plan_path = ctx.workflow_dir / "stage-plan.json"
    plan_path.write_text(json.dumps(plan_dict, indent=2), encoding="utf-8")

    phase_count = len(plan.phases)
    task_count = sum(len(p.tasks) for p in plan.phases)
    parallel_phases = sum(1 for p in plan.phases if p.parallel)

    return CommandResult(
        state=state,
        message=(
            f"stage-plan: {state.current_stage.value} — "
            f"{phase_count} phases, {task_count} tasks, {parallel_phases} parallel"
        ),
        ml_outputs=plan_dict,
        artifacts_written=["stage-plan.json"],
    )


# ── impl-schedule ─────────────────────────────────────────────────────────────

@register("impl-schedule")
def handle_impl_schedule(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """
    Compute DAG-driven story implementation schedule.
    Returns parallel story groups with TDD phases per story.
    """
    from shapeitup.core.impl_scheduler import compute_impl_schedule

    security_active = (
        team is not None and
        any(r.name == "security-reviewer" for r in team.roles)
    )

    schedule = compute_impl_schedule(
        workflow_dir=ctx.workflow_dir,
        security_active=security_active,
    )
    schedule_dict = schedule.to_dict()

    ctx.workflow_dir.mkdir(parents=True, exist_ok=True)
    sched_path = ctx.workflow_dir / "impl-schedule.json"
    sched_path.write_text(json.dumps(schedule_dict, indent=2), encoding="utf-8")

    if schedule.errors:
        return CommandResult(
            state=state,
            ok=False,
            message=f"impl-schedule: {schedule.errors[0]}",
            warnings=schedule.errors,
            ml_outputs=schedule_dict,
        )

    group_summary = ", ".join(
        f"Group {g.group}: {len(g.stories)} stories"
        for g in schedule.groups
    )

    return CommandResult(
        state=state,
        message=(
            f"impl-schedule: {schedule.total_stories} stories in "
            f"{len(schedule.groups)} groups — {group_summary}"
        ),
        ml_outputs=schedule_dict,
        artifacts_written=["impl-schedule.json"],
    )


# ── po-review ─────────────────────────────────────────────────────────────────

@register("po-review")
def handle_po_review(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Product Owner reviews stage artifacts for business value and scope alignment."""
    stage = state.current_stage.value
    artifact_path = f"reviews/product-owner-review-{stage}.md"

    capabilities = _read_artifact(ctx.workflow_dir, "capabilities.md")
    stories = _read_artifact(ctx.workflow_dir, "stories.md")
    design = _read_artifact(ctx.workflow_dir, "design-seed.md")

    context_parts = []
    if capabilities:
        context_parts.append(f"### capabilities.md\n{capabilities}")
    if stories:
        context_parts.append(f"### stories.md\n{stories}")
    if design:
        context_parts.append(f"### design-seed.md\n{design}")
    if ctx.reason:
        context_parts.append(f"### Additional context\n{ctx.reason}")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the Product Owner for '{ctx.slug}' at stage '{stage}'.\n\n"
            "Your job: review the stage artifacts for business value alignment, "
            "scope correctness, and AC-to-user-need mapping.\n\n"
            "Ask yourself:\n"
            "- Does each story trace to a real user need?\n"
            "- Are acceptance criteria written from the user's perspective, not the developer's?\n"
            "- Is anything in scope that shouldn't be? Is anything missing?\n"
            "- Are non-goals explicit?\n\n"
            f"Artifacts to review:\n{'---'.join(context_parts)}\n\n"
            f"Write your review to: {artifact_path}\n\n"
            "Format:\n"
            "## Product Owner Review — {stage}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### Business value assessment\n"
            "### Scope findings\n"
            "### AC quality findings\n"
            "### Blocking findings (if any)\n"
            "### Changes requested (if any)"
        ),
        message=f"po-review: delegating to PO agent for stage '{stage}'",
        artifacts_written=[artifact_path],
    )


# ── tl-review ─────────────────────────────────────────────────────────────────

@register("tl-review")
def handle_tl_review(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Tech Lead reviews stage artifacts for architecture, boundaries, risk."""
    stage = state.current_stage.value
    artifact_path = f"reviews/tech-lead-review-{stage}.md"

    design = _read_artifact(ctx.workflow_dir, "design-seed.md")
    spec = _read_artifact(ctx.workflow_dir, "openspec.md", "spec.md")
    plan = _read_artifact(ctx.workflow_dir, "implementation-plan.md")
    capabilities = _read_artifact(ctx.workflow_dir, "capabilities.md")

    context_parts = []
    for label, content in [
        ("design-seed.md", design), ("openspec.md", spec),
        ("implementation-plan.md", plan), ("capabilities.md", capabilities),
    ]:
        if content:
            context_parts.append(f"### {label}\n{content}")
    if ctx.reason:
        context_parts.append(f"### Additional context\n{ctx.reason}")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the Tech Lead for '{ctx.slug}' at stage '{stage}'.\n\n"
            "Your job: review stage artifacts for architectural correctness, "
            "boundary clarity, dependency sequencing, and integration risk.\n\n"
            "Ask yourself:\n"
            "- Are system boundaries clean? Is coupling minimised?\n"
            "- Does the dependency order make sense? Are there hidden circular deps?\n"
            "- What integration risks exist at stage boundaries?\n"
            "- Are ML classifier fallback paths designed correctly?\n"
            "- Is the interface contract complete and consistent?\n\n"
            f"Artifacts to review:\n{'---'.join(context_parts)}\n\n"
            f"Write your review to: {artifact_path}\n\n"
            "Format:\n"
            "## Tech Lead Review — {stage}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### Architecture assessment\n"
            "### Dependency and sequencing findings\n"
            "### Integration risk findings\n"
            "### Blocking findings (if any)\n"
            "### Changes requested (if any)"
        ),
        message=f"tl-review: delegating to Tech Lead agent for stage '{stage}'",
        artifacts_written=[artifact_path],
    )


# ── qa-review ─────────────────────────────────────────────────────────────────

@register("qa-review")
def handle_qa_review(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """QA Engineer reviews stage artifacts for testability."""
    stage = state.current_stage.value
    artifact_path = f"reviews/qa-engineer-review-{stage}.md"

    stories = _read_artifact(ctx.workflow_dir, "stories.md")
    spec = _read_artifact(ctx.workflow_dir, "openspec.md", "spec.md")
    capabilities = _read_artifact(ctx.workflow_dir, "capabilities.md")

    context_parts = []
    for label, content in [
        ("stories.md", stories), ("openspec.md", spec), ("capabilities.md", capabilities)
    ]:
        if content:
            context_parts.append(f"### {label}\n{content}")
    if ctx.reason:
        context_parts.append(f"### Additional context\n{ctx.reason}")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the QA Engineer for '{ctx.slug}' at stage '{stage}'.\n\n"
            "Your job: review stage artifacts for testability.\n\n"
            "Ask yourself:\n"
            "- Can every AC be verified by a test? If not, block.\n"
            "- Are edge cases and error conditions specified?\n"
            "- Are fallback paths documented?\n"
            "- Is regression risk understood?\n"
            "- Are there any untestable ACs that must be rewritten?\n\n"
            f"Artifacts to review:\n{'---'.join(context_parts)}\n\n"
            f"Write your review to: {artifact_path}\n\n"
            "Format:\n"
            "## QA Review — {stage}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### Testability assessment (per story)\n"
            "### Missing test coverage\n"
            "### Edge cases not covered\n"
            "### Blocking findings (if any — e.g. untestable ACs)\n"
            "### Changes requested (if any)"
        ),
        message=f"qa-review: delegating to QA agent for stage '{stage}'",
        artifacts_written=[artifact_path],
    )


# ── security-scan ─────────────────────────────────────────────────────────────

@register("security-scan")
def handle_security_scan(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Security Reviewer scans artifacts and code for security issues."""
    stage = state.current_stage.value
    artifact_path = f"reviews/security-review-{stage}.md"

    spec = _read_artifact(ctx.workflow_dir, "openspec.md", "spec.md")
    stories = _read_artifact(ctx.workflow_dir, "stories.md")

    # Gather code snippets for implementation-stage scans
    code_snippets: list[str] = []
    if stage == "implementation":
        impl_dir = ctx.workflow_dir / "impl"
        if impl_dir.exists():
            for f in list(impl_dir.rglob("*.py"))[:10] + list(impl_dir.rglob("*.ts"))[:5]:
                try:
                    code_snippets.append(f"### {f.name}\n{f.read_text(encoding='utf-8')[:2000]}")
                except Exception:
                    pass

    context_parts = []
    for label, content in [("openspec.md", spec), ("stories.md", stories)]:
        if content:
            context_parts.append(f"### {label}\n{content}")
    context_parts.extend(code_snippets)
    if ctx.reason:
        context_parts.append(f"### Additional context\n{ctx.reason}")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the Security Reviewer for '{ctx.slug}' at stage '{stage}'.\n\n"
            "Your job: review all artifacts and code for security vulnerabilities.\n\n"
            "Check for:\n"
            "- Auth flows: are permissions validated at every boundary?\n"
            "- Input validation: are all inputs sanitised before use?\n"
            "- Data exposure: does any API return more data than needed?\n"
            "- Secret handling: are secrets in env vars, never in code or logs?\n"
            "- Path traversal: are file paths validated?\n"
            "- SQL/command injection: are queries parameterised?\n"
            "- API security: is rate limiting, CORS, and auth documented?\n\n"
            f"Artifacts and code:\n{'---'.join(context_parts)}\n\n"
            f"Write your review to: {artifact_path}\n\n"
            "Format:\n"
            "## Security Review — {stage}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### HIGH findings (block gate if any exist)\n"
            "### MEDIUM findings (approve-with-changes)\n"
            "### LOW findings (informational)\n"
            "### Changes required (if any)"
        ),
        message=f"security-scan: delegating to Security Reviewer agent for stage '{stage}'",
        artifacts_written=[artifact_path],
    )


# ── qa-test-spec ──────────────────────────────────────────────────────────────

@register("qa-test-spec")
def handle_qa_test_spec(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """QA writes failing test specs for a story — TDD step 1."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="qa-test-spec: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    artifact_path = f"reviews/qa-test-spec-{slug}.md"

    stories_content = _read_artifact(ctx.workflow_dir, "stories.md")
    spec_content = _read_artifact(ctx.workflow_dir, "openspec.md", "spec.md")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the QA Engineer for '{ctx.slug}'.\n\n"
            f"Write FAILING test specifications for story: {story}\n\n"
            "Rules:\n"
            "- Tests must be runnable Python (pytest) or the project's test framework\n"
            "- Tests MUST fail before any implementation exists\n"
            "- Cover: every AC, happy path, all edge cases, error conditions\n"
            "- Use descriptive test names that map to ACs\n"
            "- Include setup/teardown if needed\n"
            "- Mark tests with @pytest.mark.xfail or equivalent if needed\n\n"
            f"Story context:\n{stories_content}\n\n"
            f"Interface spec:\n{spec_content}\n\n"
            f"Write test spec to: {artifact_path}\n"
            "Also write the actual test file to: "
            f"tests/stories/{slug}_test.py\n\n"
            "Format for spec file:\n"
            "## Test Spec — {story}\n"
            "### Test cases\n"
            "| Test name | AC covered | Expected behaviour |\n"
            "### Test file location: tests/stories/{slug}_test.py\n"
            "### Notes on setup required"
        ),
        message=f"qa-test-spec: delegating to QA agent for story '{story[:50]}'",
        artifacts_written=[artifact_path, f"tests/stories/{slug}_test.py"],
    )


# ── pair-propose ──────────────────────────────────────────────────────────────

@register("pair-propose")
def handle_pair_propose(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Implementer A (Proposer) proposes an implementation plan for a story."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="pair-propose: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    artifact_path = f"reviews/pair-propose-{slug}.md"
    test_spec = _read_artifact(ctx.workflow_dir, f"reviews/qa-test-spec-{slug}.md")
    stories_content = _read_artifact(ctx.workflow_dir, "stories.md")
    spec_content = _read_artifact(ctx.workflow_dir, "openspec.md")
    plan_content = _read_artifact(ctx.workflow_dir, "implementation-plan.md")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are Implementer A (Proposer) for '{ctx.slug}'.\n\n"
            f"Propose an implementation for story: {story}\n\n"
            "Your proposal must:\n"
            "1. Make all the QA test specs pass\n"
            "2. Declare every file you intend to write or modify\n"
            "3. Explain key design decisions\n"
            "4. Identify any risks or assumptions\n"
            "5. Stay within the interface contracts in openspec.md\n\n"
            f"QA test spec:\n{test_spec}\n\n"
            f"Story:\n{stories_content}\n\n"
            f"Interface spec:\n{spec_content}\n\n"
            f"Implementation plan context:\n{plan_content}\n\n"
            f"Write your proposal to: {artifact_path}\n\n"
            "Format:\n"
            "## Implementation Proposal — {story}\n"
            "### Files to write/modify\n"
            "| File | Change | Reason |\n"
            "### Design decisions\n"
            "### Risks and assumptions\n"
            "### How tests will pass\n"
            "### Open questions for Challenger"
        ),
        message=f"pair-propose: delegating to Proposer agent for story '{story[:50]}'",
        artifacts_written=[artifact_path],
    )


# ── pair-challenge ────────────────────────────────────────────────────────────

@register("pair-challenge")
def handle_pair_challenge(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Implementer B (Challenger) challenges the proposal and must reach consensus."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="pair-challenge: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    artifact_path = f"reviews/pair-challenge-{slug}.md"
    proposal = _read_artifact(ctx.workflow_dir, f"reviews/pair-propose-{slug}.md")
    test_spec = _read_artifact(ctx.workflow_dir, f"reviews/qa-test-spec-{slug}.md")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are Implementer B (Challenger) for '{ctx.slug}'.\n\n"
            f"Review the Proposer's implementation plan for story: {story}\n\n"
            "Your job is to challenge, not obstruct. Be specific and constructive.\n\n"
            "Review for:\n"
            "- Correctness: will the tests actually pass with this approach?\n"
            "- Coupling: is the implementation tightly coupled where it doesn't need to be?\n"
            "- Edge cases: has the Proposer missed any test cases?\n"
            "- Complexity: is there a simpler approach that achieves the same result?\n"
            "- File ownership: are write paths declared and non-conflicting?\n"
            "- Risk: what could go wrong that the Proposer hasn't considered?\n\n"
            f"Proposer's plan:\n{proposal}\n\n"
            f"QA test spec:\n{test_spec}\n\n"
            f"Write your challenge response to: {artifact_path}\n\n"
            "Format:\n"
            "## Challenger Response — {story}\n"
            "### Overall verdict: agree | agree-with-changes | disagree\n"
            "### Points of agreement\n"
            "### Challenges and requested changes\n"
            "  (each with: finding | severity: high/medium/low | suggested resolution)\n"
            "### Blocking findings (if any — disagreement must be resolved before coding)\n"
            "### Consensus implementation approach (if agree or agree-with-changes)"
        ),
        message=f"pair-challenge: delegating to Challenger agent for story '{story[:50]}'",
        artifacts_written=[artifact_path],
    )


# ── pair-implement ────────────────────────────────────────────────────────────

@register("pair-implement")
def handle_pair_implement(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Write final consensus implementation after pair programming discussion."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="pair-implement: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    proposal = _read_artifact(ctx.workflow_dir, f"reviews/pair-propose-{slug}.md")
    challenge = _read_artifact(ctx.workflow_dir, f"reviews/pair-challenge-{slug}.md")
    test_spec = _read_artifact(ctx.workflow_dir, f"reviews/qa-test-spec-{slug}.md")
    stories_content = _read_artifact(ctx.workflow_dir, "stories.md")
    spec_content = _read_artifact(ctx.workflow_dir, "openspec.md")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the Implementer for '{ctx.slug}'.\n\n"
            f"Write the final consensus implementation for story: {story}\n\n"
            "You have the Proposer's plan and the Challenger's response. "
            "Incorporate all agreed changes. Resolve all Challenger findings.\n\n"
            "Rules:\n"
            "1. Make every failing test in the QA test spec pass\n"
            "2. Only write files declared in the proposal (updated for Challenger changes)\n"
            "3. Keep changes minimal — only what's needed for this story\n"
            "4. Write clean, readable code with inline comments for non-obvious logic\n"
            "5. Do not introduce new dependencies without a strong reason\n\n"
            f"Proposer plan:\n{proposal}\n\n"
            f"Challenger response:\n{challenge}\n\n"
            f"QA test spec:\n{test_spec}\n\n"
            f"Story:\n{stories_content}\n\n"
            f"Interface spec:\n{spec_content}\n\n"
            "Write the implementation files directly. "
            "After writing, confirm which test cases now pass."
        ),
        message=f"pair-implement: delegating to Implementer for story '{story[:50]}'",
    )


# ── tl-impl-review ────────────────────────────────────────────────────────────

@register("tl-impl-review")
def handle_tl_impl_review(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """Tech Lead reviews the implemented code for a story."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="tl-impl-review: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    artifact_path = f"reviews/tl-impl-review-{slug}.md"
    proposal = _read_artifact(ctx.workflow_dir, f"reviews/pair-propose-{slug}.md")
    challenge = _read_artifact(ctx.workflow_dir, f"reviews/pair-challenge-{slug}.md")
    design = _read_artifact(ctx.workflow_dir, "design-seed.md")

    # Gather code from impl dir
    code_snippets: list[str] = []
    impl_dir = ctx.workflow_dir / "impl" / slug
    if impl_dir.exists():
        for f in list(impl_dir.rglob("*.*"))[:8]:
            if f.suffix in (".py", ".ts", ".js", ".go", ".java"):
                try:
                    code_snippets.append(f"### {f.name}\n{f.read_text(encoding='utf-8')[:3000]}")
                except Exception:
                    pass

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the Tech Lead for '{ctx.slug}'.\n\n"
            f"Review the implementation for story: {story}\n\n"
            "Check for:\n"
            "- Design conformance: does implementation match design-seed.md intent?\n"
            "- Architecture: is coupling minimised, boundaries respected?\n"
            "- TDD adherence: were tests written first (check qa-test-spec dates)?\n"
            "- Code quality: readable, maintainable, appropriately commented?\n"
            "- Design drift: does code diverge from the approved design?\n"
            "- Pair programming resolution: were all Challenger findings addressed?\n\n"
            f"Pair proposal:\n{proposal}\n\n"
            f"Pair challenge response:\n{challenge}\n\n"
            f"Design seed:\n{design}\n\n"
            f"Implementation code:\n{'---'.join(code_snippets) if code_snippets else 'No code files found in impl/ dir'}\n\n"
            f"Write your review to: {artifact_path}\n\n"
            "Format:\n"
            "## Tech Lead Implementation Review — {story}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### Architecture conformance\n"
            "### Code quality findings\n"
            "### Design drift findings\n"
            "### Blocking findings (if any)\n"
            "### Changes requested (if any)"
        ),
        message=f"tl-impl-review: delegating to Tech Lead for story '{story[:50]}'",
        artifacts_written=[artifact_path],
    )


# ── qa-validate ───────────────────────────────────────────────────────────────

@register("qa-validate")
def handle_qa_validate(
    state: WorkflowState,
    ctx: CommandContext,
    team: ActiveTeam | None,
) -> CommandResult:
    """QA validates that tests pass and ACs are covered post-implementation."""
    story = ctx.reason.strip() or ctx.items.strip()
    if not story:
        return CommandResult(
            state=state,
            ok=False,
            message="qa-validate: provide story name as --reason",
            warnings=["No story specified"],
        )

    slug = _slug(story)
    artifact_path = f"reviews/qa-validate-{slug}.md"
    test_spec = _read_artifact(ctx.workflow_dir, f"reviews/qa-test-spec-{slug}.md")
    stories_content = _read_artifact(ctx.workflow_dir, "stories.md")

    return CommandResult(
        state=state,
        needs_llm=True,
        llm_task=(
            f"You are the QA Engineer for '{ctx.slug}'.\n\n"
            f"Validate the implementation for story: {story}\n\n"
            "Your job:\n"
            "1. Run the test suite for this story\n"
            "2. Confirm every AC has test evidence\n"
            "3. Check coverage — are edge cases tested?\n"
            "4. Identify any regressions introduced\n"
            "5. Approve if all tests pass and all ACs are covered\n\n"
            f"Original test spec:\n{test_spec}\n\n"
            f"Story:\n{stories_content}\n\n"
            f"Write your validation report to: {artifact_path}\n\n"
            "Format:\n"
            "## QA Validation — {story}\n"
            "### Verdict: approve | approve-with-changes | block\n"
            "### Test results (pass/fail per test case)\n"
            "### AC coverage (each AC mapped to test)\n"
            "### Missing coverage\n"
            "### Regressions detected (if any)\n"
            "### Blocking findings (if any)"
        ),
        message=f"qa-validate: delegating to QA agent for story '{story[:50]}'",
        artifacts_written=[artifact_path],
    )
