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

### Step 3 — Gate check

After all phases complete, run:
```bash
python -m shapeitup.cli --slug <slug> --root <root> --command review-sync --output-format json
```

If gate is clear → render state and offer `shapeitup:approve`.
If gate is not clear → show which roles are pending, offer to re-run those agents.

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
Write tests: `tests/stories/<story-slug>_test.py`
Tests MUST fail before implementation.

### `pair-propose`
You are Implementer A (Proposer). Read test spec + story → propose implementation.
Write: `.workflow/<slug>/reviews/pair-propose-<story-slug>.md`

### `pair-challenge`
You are Implementer B (Challenger). Read proposal → challenge and respond.
Write: `.workflow/<slug>/reviews/pair-challenge-<story-slug>.md`

### `pair-implement`
You are the Implementer. Read proposal + challenge → write final consensus code.
Make all failing tests pass. Write the actual source files.

### `tl-impl-review`
You are the Tech Lead. Review implemented code for architecture conformance, TDD adherence, design drift.
Write: `.workflow/<slug>/reviews/tl-impl-review-<story-slug>.md`

### `qa-validate`
You are the QA Engineer. Run tests, check all ACs covered, identify regressions.
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

1. **Never call `approve` until `review-sync` confirms all review artifacts exist.**
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
