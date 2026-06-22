---
name: shapeitup
description: >
  Use when the user writes shapeitup:<command> or asks to start, continue, or
  run a staged engineering delivery workflow. Handles all shapeitup commands
  including parallel agent orchestration, TDD pair programming, and role-based
  review. Each role (Product Owner, Tech Lead, QA, Security Reviewer) is an
  active agent that does real work — not just a checkbox.
---

# shapeitup skill

## Architecture contract

| Layer | Owns | Never touches |
|-------|------|---------------|
| Python core (`python -m shapeitup.cli`) | Gate enforcement, team assembly, ML analysis, state, scheduling | Content generation |
| This skill (LLM orchestrator) | Content generation, agent spawning, pair programming | Gate decisions, stage routing, team composition |

**Python is the authority.** The skill reads stage-plan JSON from Python and acts on it. It never decides whether a gate passes or what stage comes next.

---

## Invocation patterns

```
shapeitup:<command> ["<argument>"]
shapeitup:run               # run the full current stage
shapeitup:run --checkpoints   # pause before each stage advance for human sign-off
shapeitup:approve           # gate pass
shapeitup:capability-synth  # generate capabilities doc
shapeitup:pair-implement "Story 3: Add webhook handler"
```

---

## CLI invocation

```bash
python -m shapeitup.cli \
  --slug  <slug>   \
  --root  <path>   \
  --command <cmd>  \
  [--reason  "..."] \
  [--role    "..."] \
  [--verdict "..."] \
  [--findings "..."] \
  [--items   "..."] \
  [--output-format json]
```

Always pass `--output-format json`. Parse `result["ok"]`, `result["needs_llm"]`, `result["llm_task"]`, `result["ml_outputs"]`.

---

## `shapeitup:run` — Full stage orchestration

When the user says `shapeitup:run` (or equivalent), execute the full current stage:

### Step 1 — Get the plan

```bash
python -m shapeitup.cli --slug <slug> --root <root> --command stage-plan --output-format json
```

Parse `result["ml_outputs"]` → `plan`. It has this shape:

```json
{
  "stage": "discuss",
  "phases": [
    {
      "phase": 1,
      "label": "Generate stage artifact",
      "parallel": false,
      "tasks": [{"role": "system", "command": "capability-synth", ...}]
    },
    {
      "phase": 2,
      "label": "Parallel role reviews",
      "parallel": true,
      "tasks": [
        {"role": "product-owner", "command": "po-review", ...},
        {"role": "tech-lead",     "command": "tl-review", ...},
        {"role": "qa-engineer",   "command": "qa-review", ...}
      ]
    },
    {
      "phase": 3,
      "label": "Gate check",
      "parallel": false,
      "tasks": [{"role": "system", "command": "review-sync", ...}]
    }
  ]
}
```

### Step 2 — Execute phases in order

For each phase:

**If `parallel: false`** → execute tasks sequentially.
- For each task: if `needs_llm: true`, perform the LLM work described in the command section below. If `needs_llm: false`, call the Python CLI directly.

**If `parallel: true`** → spawn all tasks as parallel agents using the Agent tool.
- Send a single message with one Agent tool call per task.
- Each agent: calls the Python CLI for its command, performs the LLM synthesis, writes the artifact.
- Wait for all agents to complete before moving to the next phase.

### Step 3 — Gate check (auto-advance)

After all phases complete, run:
```bash
python -m shapeitup.cli --slug <slug> --root <root> --command review-sync --output-format json
```

Parse `result["ml_outputs"]` and act based on `auto_advance` and `stop_reason`:

| `auto_advance` | `stop_reason` | Action |
|---|---|---|
| `true` | `null` | Auto-call `approve` — no human needed |
| `false` | `"pending_reviews"` | Show pending roles, offer to re-run their agents |
| `false` | `"role_block"` | Show blocking role findings, ask user how to proceed |
| `false` | `"security_high"` | **Stop. Surface security findings. Do not proceed without explicit user instruction.** |
| `false` | `"no_team"` | Error — team not initialized |

**Auto-advance** (when `auto_advance: true`):
```bash
python -m shapeitup.cli --slug <slug> --root <root> --command approve --reason "all reviews passed" --output-format json
```
Then render the state table and announce the stage has advanced.

**`--checkpoints` flag**: If the user ran `shapeitup:run --checkpoints`, always pause before `approve` even when `auto_advance: true`. Show the gate summary and ask "Advance to next stage?".

---

## Implementation stage — `shapeitup:run` at implementation

Implementation is different. The plan references `impl-schedule`.

### Step 1 — Get story groups

```bash
python -m shapeitup.cli --slug <slug> --root <root> --command impl-schedule --output-format json
```

Parse `result["ml_outputs"]["groups"]`:
```json
[
  {"group": 1, "stories": ["Story 1", "Story 2"], "notes": ["run in parallel"]},
  {"group": 2, "stories": ["Story 3"], "notes": []}
]
```

### Step 2 — Implement group by group (sequential between groups)

For each group, **spawn parallel agents** — one per story in the group.

Each story agent runs the TDD pipeline:

```
Phase 1 — QA writes failing tests (sequential, must complete first)
  → call qa-test-spec
  → QA agent writes tests/stories/<slug>_test.py

Phase 2 — Pair programming (sequential within story, parallel across stories)
  → call pair-propose  (Proposer agent)
  → call pair-challenge (Challenger agent reads proposal, responds)
  → if challenger verdict is "disagree":
       run one more round (pair-propose reads challenge, amends)
       run pair-challenge again (max 3 rounds total)
  → if still unresolved: surface to user
  → call pair-implement (write the final code)

Phase 3 — Parallel validation (parallel within story)
  → spawn 3 agents simultaneously:
       qa-validate     (QA confirms tests pass)
       tl-impl-review  (Tech Lead reviews code)
       security-scan   (Security if activated)
```

Group N+1 only starts after Group N's validation phase completes.

### After all groups complete — advance the workflow

Once every story group has finished Phase 3 (validation), run:
```bash
python -m shapeitup.cli --slug <slug> --root <root> --command impl-complete --reason "all stories implemented" --output-format json
```
This writes `implementation-manifest.md` listing every validated story. Then call `next` to advance to the review stage:
```bash
python -m shapeitup.cli --slug <slug> --root <root> --command next --output-format json
```
**If code was written outside the TDD flow** (e.g. directly in the editor), still run `impl-complete` — it will warn about missing validation artifacts. You can then either run the validations manually or use `shapeitup:override` to skip.

---

## Pair programming protocol

When spawning pair programming agents, use this structured dialogue:

### Proposer agent prompt template

```
You are Implementer A (Proposer) working on <slug>, story: <story>.

Read the QA test spec and propose an implementation that makes all tests pass.

QA test spec: <content of reviews/qa-test-spec-<slug>.md>
Story: <relevant story from stories.md>
Interface spec: <openspec.md>
Implementation plan: <implementation-plan.md>

Your proposal must:
1. List every file you will write or modify
2. Explain key design decisions
3. Identify risks and assumptions
4. Show how each test case will pass

Write your proposal to: reviews/pair-propose-<slug>.md
```

### Challenger agent prompt template

```
You are Implementer B (Challenger) working on <slug>, story: <story>.

Review the Proposer's plan. Be specific and constructive — challenge to improve, not block.

Proposal: <content of reviews/pair-propose-<slug>.md>
QA test spec: <content of reviews/qa-test-spec-<slug>.md>

Check for: test coverage, coupling, edge cases, complexity, file ownership conflicts, risks.

Your response must have:
- Overall verdict: agree | agree-with-changes | disagree
- For agree-with-changes: specific changes (each with severity high/med/low)
- For disagree: blocking finding that must be resolved before coding

Write your response to: reviews/pair-challenge-<slug>.md
```

### Consensus rule

- `agree` → proceed directly to `pair-implement`
- `agree-with-changes` → proceed to `pair-implement` (Implementer incorporates changes)
- `disagree` → one more round (max 3 total). If still disagreeing after 3 rounds, surface to user with both positions.

---

## Per-command LLM task reference

### `capability-synth`
Read: existing capabilities.md (if any), repo README, design docs.
Write: `.workflow/<slug>/capabilities.md`
Format: `# Capabilities — <slug>` / `## In scope` / `## Out of scope` / `## Open questions`

### `design-synth`
Read: capabilities.md, repo codebase entrypoints.
Write: `.workflow/<slug>/design-seed.md`
Format: Problem statement / Proposed approach / Key interfaces and contracts / Risks / Unknowns

### `story-synth`
Read: capabilities.md, design-seed.md.
Write: `.workflow/<slug>/stories.md`
Each story: `## Story N: <title>` / Goal / Value / ACs (testable, observable) / Dependencies (Depends on:) / Test hints

### `story-enrichment-synth`
Read + update in place: stories.md
Add per story: edge cases, validation requirements, error handling, cross-cutting concerns

### `openspec-synth`
Read: stories.md, design-seed.md, execution-path.json (interface signal count).
Write: `.workflow/<slug>/openspec.md`
Format: API endpoints/functions / Data models / Events / Auth and permissions

### `implementation-plan-synth`
Read: stories.md, openspec.md, design-seed.md.
Write: `.workflow/<slug>/implementation-plan.md`
Format: Build order with rationale / Parallel tracks / Integration checkpoints / Risk items / Definition of done

### `po-review`
You are the Product Owner. Review business value, scope, AC-to-user-need mapping.
Write: `.workflow/<slug>/reviews/product-owner-review-<stage>.md`
Verdict: approve | approve-with-changes | block

### `tl-review`
You are the Tech Lead. Review architecture, boundaries, dependency sequencing, integration risk.
Write: `.workflow/<slug>/reviews/tech-lead-review-<stage>.md`
Verdict: approve | approve-with-changes | block

### `qa-review`
You are the QA Engineer. Review testability — can every AC be verified by a test?
Write: `.workflow/<slug>/reviews/qa-engineer-review-<stage>.md`
Verdict: approve | approve-with-changes | block. Block if any AC is untestable.

### `security-scan`
You are the Security Reviewer. Check auth, input validation, data exposure, secrets, injection.
Write: `.workflow/<slug>/reviews/security-review-<stage>.md`
Block if any HIGH finding.

### `qa-test-spec`
You are the QA Engineer. Write failing test specifications BEFORE any implementation.
Write spec: `.workflow/<slug>/reviews/qa-test-spec-<story-slug>.md`
Write tests to the **project root** using the language-appropriate path and framework (see Project language detection table above). Examples:
- Python: `tests/stories/<story-slug>_test.py`
- Scala: `src/test/scala/<package>/<StorySlugSpec>.scala` — extend `CatsEffectSuite` if using CatsEffect
- TypeScript: `src/__tests__/<story-slug>.test.ts`
Tests MUST fail (or not compile) before any implementation is written. Confirm failure before handing off to pair-propose.

### `pair-propose`
You are Implementer A (Proposer). Read test spec + story → propose implementation.
Write: `.workflow/<slug>/reviews/pair-propose-<story-slug>.md`

### `pair-challenge`
You are Implementer B (Challenger). Read proposal → challenge and respond.
Write: `.workflow/<slug>/reviews/pair-challenge-<story-slug>.md`

### `pair-implement`
You are the Implementer. Read proposal + challenge → write final consensus code.
**Write actual source files to the project root** using language-appropriate locations (see Project language detection table). For Scala: `src/main/scala/<package>/`. NOT inside `.workflow/` — these are deliverable files.
For Scala/CatsEffect: match the effect system the project uses. Read existing files in the same package before writing — replicate import style, type aliases, and any custom `App`/`Effect` traits. Never introduce `Future` or mutable state.
Make all failing tests pass. Run the test command and confirm results.

### `tl-impl-review`
You are the Tech Lead. Review implemented code for architecture conformance, TDD adherence, design drift.
Write: `.workflow/<slug>/reviews/tl-impl-review-<story-slug>.md`

### `qa-validate`
You are the QA Engineer. Run tests, check all ACs covered, identify regressions.
Use the language-appropriate test runner (see Project language detection table). For Scala: `sbt testOnly *<SpecName>` to run only the story's tests, then `sbt test` for the full suite to check for regressions.
Write: `.workflow/<slug>/reviews/qa-validate-<story-slug>.md`

---

## Rendering state

After any command:

```
── shapeitup: <slug> ──────────────────────────────────
  Stage      : <stage>
  Gate       : <gate_status>
  Team       : PO ✓  TL ✓  QA ⏳  (✓=done ✗=blocked ⏳=pending)
  Next action: <next_action>
  Message    : <message>
───────────────────────────────────────────────────────
```

For ML outputs (`ci-feedback`, `execution-path`, `drift-check`), surface:
- ci-feedback: class, severity, retryable, confidence
- execution-path: path type, complexity, security/interface signals
- drift-check: drift type, score, needs reconciliation

For `stage-plan`: render as a phase table, highlight parallel phases.
For `impl-schedule`: render as a group table with story counts.

---

## Gate rules

1. **Auto-call `approve` when `review-sync` returns `auto_advance: true`.** Use `--checkpoints` to always pause.
2. **Never write a review artifact on behalf of a role unless you are that agent.** Each role review is done by a dedicated agent with that role's perspective.
3. **`override` requires a reason.** Ask before using it.
4. **Pair programming disagreement**: surface to user after 3 rounds. Do not auto-resolve.
5. **Security blocking findings**: always surface to user. Never mark as low-priority.

---

## Stage flow reference

```
discuss            → [stage-run: capability-synth + PO/TL/QA review] → approve
capability-review  → [stage-run: capability-synth + PO/TL/QA review] → approve
epic-shaping       → [stage-run: design-synth + PO/TL/QA review]     → approve
story-slicing      → [stage-run: story-synth + PO/TL/QA review]      → approve
story-enrichment   → [stage-run: enrichment-synth + PO/TL/QA review] → approve
spec-authoring     → [stage-run: openspec-synth + PO/TL/QA review]   → approve
impl-planning      → [impl-plan-synth + review] → next (no gate)
implementation     → [impl-schedule → per-story TDD groups] → next
review             → [stage-run: feedback-synth + PO/TL/QA review]   → approve
release-planning   → [stage-run: feedback-synth + reviews]            → approve → done
```

Security Reviewer joins when Python signals: security_signal OR flagged OR ≥3 interfaces.

---

## Project language detection

Before running any implementation command, detect the project language from the repo root:

| Signal | Language | Test runner | Test file pattern | Source location |
|--------|----------|-------------|-------------------|-----------------|
| `build.sbt` or `*.scala` | Scala | `sbt test` or `sbt testOnly *ClassName` | `src/test/scala/**/*Spec.scala` | `src/main/scala/` |
| `package.json` | TypeScript/JS | `npm test` or `npx jest` | `*.test.ts` or `*.spec.ts` | `src/` |
| `go.mod` | Go | `go test ./...` | `*_test.go` | same package |
| `Cargo.toml` | Rust | `cargo test` | `#[cfg(test)]` in same file | `src/` |
| `pyproject.toml` / `setup.py` | Python | `python -m pytest` | `test_*.py` | package dir |
| `pom.xml` or `build.gradle` | Java/Kotlin | `mvn test` or `./gradlew test` | `*Test.java` / `*Spec.kt` | `src/test/` |

**Detect once at startup, store as `project_lang`. Apply to all qa-test-spec, pair-implement, and qa-validate commands.**

### Scala / CatsEffect specifics

When `project_lang = scala`:

- **Test framework**: check for MUnit (`munit` in `build.sbt`), ScalaTest (`scalatest`), or Specs2. Default to MUnit if unclear.
- **CatsEffect tests**: extend `CatsEffectSuite` (MUnit) or use `IOSpec` trait. Wrap IO assertions in `assertIO(...)` or `.assertEquals(...)`.
- **Effect system**: if the repo has a custom `Effect` or `App` trait on top of CatsEffect, read its definition before proposing implementations — do not bypass it.
- **Test file location**: `src/test/scala/<package>/<StoryNameSpec>.scala`
- **Source file location**: `src/main/scala/<package>/`
- **Build tool**: `sbt` by default. Check for `mill` (`build.sc` present).
- **Imports**: always use the project's existing import style (check 2-3 existing files before writing any new file).
- **Functional patterns**: prefer `IO`, `Resource`, `Ref`, `Queue`, `Stream` from fs2 where applicable. Avoid `var`, mutable state, and `Future`.

---

## Startup

When a user starts shapeitup without a specific command:
1. Call `actions` to check current state
2. If new: ask for slug + feature description, then offer `shapeitup:run`
3. If existing: render state table, offer `shapeitup:run` to continue
4. Always say which stage is current and what `shapeitup:run` will do

---

## Error handling

| Error | Action |
|-------|--------|
| `Command not allowed in stage` | Show allowed commands, suggest correct one |
| `Waiting for review from: <roles>` | Tell user which roles are pending, offer to run them |
| `ok: false` | Surface full message, do not auto-recover |
| Pair programming disagreement after 3 rounds | Surface both positions to user |
| Security HIGH finding | Surface immediately, do not proceed without user acknowledgement |
