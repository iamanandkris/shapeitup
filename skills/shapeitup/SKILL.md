---
name: shapeitup
description: >
  Use when the user writes shapeitup:<command>, asks to start or continue a
  staged engineering delivery workflow, wants ML-assisted story classification,
  CI failure analysis, design-drift detection, or team gate management.
  Covers all shapeitup commands: approve, reject, next, team-verdict, ci-feedback,
  execution-path, drift-check, dag-sync, capability-synth, design-synth,
  story-synth, story-enrichment-synth, openspec-synth, implementation-plan-synth,
  feedback-synth, issue-advisor, replan, verify-fix, and all record/housekeeping
  commands.
---

# shapeitup skill

## Architecture contract

shapeitup has two layers. Keep them separate:

| Layer | What it owns | Never touches |
|-------|-------------|---------------|
| Python core (`python -m shapeitup.cli`) | Gate enforcement, team verdicts, ML analysis, state persistence, stage routing | LLM synthesis |
| This skill (LLM) | Content generation — capabilities, design, stories, plans, feedback | Gate decisions, team structure, stage routing |

**The Python core is the authority.** The skill never advances a stage, records a verdict, or decides team composition. When the user writes `shapeitup:approve`, call the Python CLI — do not just say "approved".

---

## CLI invocation

```bash
python -m shapeitup.cli \
  --slug  <slug>   \   # workflow identifier, e.g. "my-epic"
  --root  <path>   \   # repo root (default: cwd)
  --command <cmd>  \   # see command list below
  [--reason  "..."] \  # free-text argument / story text / log text
  [--role    "..."] \  # role name for team-verdict (e.g. "product-owner")
  [--verdict "..."] \  # approve | approve-with-changes | block
  [--findings "..."] \ # pipe-separated blocking findings
  [--items   "..."] \  # for defer/rework-item
  [--output-format json]
```

Always pass `--output-format json` and parse the result dict:

```
{
  "ok": bool,
  "stage": str,           # current stage after command
  "gate_status": str,     # pending | approved | rejected | blocked
  "next_action": str,     # suggested next step
  "message": str,         # human-readable outcome
  "needs_llm": bool,      # true = Python wants LLM to synthesise
  "llm_task": str,        # what to synthesise (if needs_llm)
  "ml_outputs": dict,     # ML results (failure class, drift score, etc.)
  "team": dict,           # active roles + gate status
  "warnings": list[str]
}
```

If `ok` is false, surface the error and stop — do not attempt further steps.

---

## Slug and root resolution

1. If the user provides `--slug` or names an epic, use that as the slug.
2. Default slug: slugify the directory name of `--root`.
3. Root: use the current repository root. Detect by walking up from cwd for a `.git` directory. Fall back to cwd.
4. State lives at `<root>/.workflow/<slug>/state.json`. Never read or write this file directly — use the CLI.

---

## Command reference

### Mechanical commands (Python-only — call CLI, show result)

These require no LLM work. Call the CLI and render the result.

| Command | Typical invocation |
|---------|-------------------|
| `actions` | Check current stage and available commands |
| `approve` | Gate passed — advance to next stage |
| `reject` | Reject at current gate with reason |
| `next` | Non-gated advancement (e.g. impl-planning → implementation) |
| `override` | Force advance, bypassing gate (use sparingly, reason required) |
| `reconcile` | Record reconciliation note without stage change |
| `refine` | Stay at stage, record refinement |
| `rework-item` | Flag specific item for rework without full rejection |
| `proceed-only` | Advance with known gaps explicitly deferred |
| `defer` | Log deferred item for later |
| `team-verdict` | Record a role's verdict (--role, --verdict, --findings) |
| `challenge` | Log a challenge/concern from a role |
| `review-sync` | Sync gate status — check if all blocking roles have approved |
| `ci-feedback` | Classify CI failure text (ML) — pass log as --reason |
| `execution-path` | Classify story as simple/flagged (ML) — pass story as --reason |
| `drift-check` | Detect design↔code drift (ML) — reads design artifacts automatically |
| `dag-sync` | Build story dependency graph from stories.md |
| `memory-record` | Persist a memory note |
| `debt-record` | Log technical debt |
| `accounting-record` | Log an invocation entry |

### Synthesis commands (LLM generates content, then call CLI)

For synthesis commands, the flow is always:

1. Read existing artifacts for context (listed per command below)
2. Generate the content
3. Write the artifact to `.workflow/<slug>/<artifact>`
4. Call the Python CLI with the matching mechanical command to record completion

---

#### `capability-synth`

**When:** discuss stage. Generate or refine capabilities.md.

**Read first:**
- `.workflow/<slug>/design-seed.md` (if exists)
- `README.md` or `docs/design.md` (if exists)
- Existing codebase entrypoints (README, package.json/pyproject.toml, main source files)

**Generate** `.workflow/<slug>/capabilities.md`:
```markdown
# Capabilities — <slug>

## In scope
- <capability 1>: <one sentence description>
- ...

## Out of scope
- <item>: <reason>

## Open questions
- <question>
```

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "capability-synth complete"`

---

#### `design-synth`

**When:** epic-shaping stage. Generate a design seed from the epic scope.

**Read first:** capabilities.md, any linked design docs, codebase reconnaissance.

**Generate** `.workflow/<slug>/design-seed.md`:
```markdown
# Design seed — <slug>

## Problem statement
<one paragraph>

## Proposed approach
<architecture or interaction design — enough to slice stories>

## Key interfaces / contracts
<list API shapes, data models, or system boundaries>

## Risks
- <risk>: <mitigation>

## Unknowns
- <item>
```

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "design-synth complete"`

---

#### `story-synth`

**When:** story-slicing stage. Slice epic into atomic stories.

**Read first:** capabilities.md, design-seed.md, any existing stories.md.

**Generate** `.workflow/<slug>/stories.md`. Each story must follow this template exactly:

```markdown
## Story N: <title>

**Goal:** <one sentence — what changes in the system>
**Value:** <who benefits and how>

### Acceptance criteria
- [ ] <testable criterion>
- [ ] <testable criterion>

### Dependencies
Depends on: <Story N-1>, ... (omit line if none)

### Test hints
- <what to test>

---
```

Rules:
- Each story is independently deployable (no hidden coupling)
- ACs are observable, not implementation steps
- Security stories are separate and explicitly labelled
- Include a "Story 0: Setup" if scaffolding is needed
- 5–12 stories is normal; raise a concern if > 15 before slicing

**Then call:** `python -m shapeitup.cli --slug <slug> --command dag-sync` to validate the DAG, then surface any errors before proceeding.

---

#### `story-enrichment-synth`

**When:** story-enrichment stage. Add depth to stories without changing scope.

**Read first:** stories.md, design-seed.md, any existing spec files.

**Enrich each story in `.workflow/<slug>/stories.md`** by adding:
- Explicit edge cases in ACs
- Data validation requirements  
- Error handling notes
- Cross-cutting concerns (auth, logging, observability)
- Integration test hints

Do not change story goals or add scope. Rewrite the file in place.

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "story-enrichment-synth complete"`

---

#### `openspec-synth`

**When:** spec-authoring stage. Generate interface contracts.

**Read first:** stories.md, design-seed.md, execution-path.json (interface signals).

**Generate** `.workflow/<slug>/openspec.md`:
```markdown
# Interface Contracts — <slug>

## API endpoints / functions
### <endpoint or function name>
- Method/signature: `<>`
- Input: `<schema>`
- Output: `<schema>`
- Error cases: `<>`

## Data models
### <Model>
| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|

## Events / messages (if applicable)
...

## Auth and permissions
...
```

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "openspec-synth complete"`

---

#### `implementation-plan-synth`

**When:** spec-authoring or implementation-planning stage. Generate a concrete build plan.

**Read first:** stories.md, openspec.md, design-seed.md, execution-path.json.

**Generate** `.workflow/<slug>/implementation-plan.md`:
```markdown
# Implementation plan — <slug>

## Build order
1. <Story N>: <why this first> — estimated size: <S/M/L>
2. ...

## Parallel tracks (if applicable)
- Track A: <stories>
- Track B: <stories>

## Integration points
- After Story N: <what to verify before proceeding>

## Risk items
- <story> has <risk> — mitigation: <approach>

## Definition of done
- All ACs green
- Drift check score ≥ 0.60
- No HIGH-severity CI failures unresolved
- All blocking role verdicts recorded
```

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "implementation-plan-synth complete"`

---

#### `feedback-synth`

**When:** any stage where feedback has been collected. Synthesise team notes into actionable items.

**Read first:** `.workflow/<slug>/review-log.md`, `.workflow/<slug>/ci-feedback.md`, `.workflow/<slug>/drift-check.md`.

**Generate** `.workflow/<slug>/feedback-synthesis.md`:
```markdown
# Feedback synthesis — <stage> — <date>

## Blocking items (must resolve before advancing)
- [ ] <item> — raised by <role>

## Non-blocking notes (should address)
- <item>

## Deferred (acknowledged, not blocking)
- <item>

## Recommended next command
shapeitup:<command> "<reason>"
```

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "feedback-synth complete"`

---

#### `issue-advisor`

**When:** any stage where a blocking issue needs options.

**Read first:** `.workflow/<slug>/state.json` (via `actions`), context of the specific issue from `--reason`.

**Output directly to the user** (no artifact):
1. Restate the issue in one sentence
2. Root cause hypothesis (1–2 sentences)
3. Three options, each with: approach, trade-off, effort (S/M/L)
4. Recommended option with rationale
5. Suggested shapeitup command to proceed

---

#### `replan`

**When:** implementation-planning stage, significant scope or constraint change.

**Read first:** stories.md, implementation-plan.md, any new constraints in `--reason`.

**Update** `.workflow/<slug>/implementation-plan.md` with a `## Replan — <date>` section:
- What changed
- Updated build order (only sections that changed)
- New/removed risk items

**Then call:** `python -m shapeitup.cli --slug <slug> --command reconcile --reason "replan: <summary>"`

---

#### `verify-fix`

**When:** implementation stage, after a fix has been applied.

**Read first:** `.workflow/<slug>/ci-feedback.md`, the fix description from `--reason`.

**Output directly to the user:**
1. Failure class that was addressed
2. Checklist: does the fix address the root cause? Does it introduce new risks?
3. Suggested test to confirm the fix
4. Whether to call `shapeitup:ci-feedback` again with updated log

---

## Rendering state to the user

After any command, render the result as:

```
── shapeitup: <slug> ─────────────────────────────
  Stage      : <stage>
  Gate       : <gate_status>
  Team       : <role1> ✓  <role2> ✓  <role3> ⏳  (✓=approved, ✗=blocked, ⏳=pending)
  Next action: <next_action>
  Message    : <message>
─────────────────────────────────────────────────
```

If `warnings` is non-empty, list them below.
If `ml_outputs` is non-empty and the command was an ML command, summarise:
- For `ci-feedback`: failure class, severity, retryable flag, confidence
- For `execution-path`: path type, complexity score, security/interface signals
- For `drift-check`: drift type, score, whether reconciliation is needed
- For `dag-sync`: story count, any cycle or dependency errors

---

## Gate rules — never bypass

1. **Never call `approve` if the gate is pending** without first calling `review-sync` to confirm all blocking roles have recorded verdicts. If not, tell the user which roles are pending.
2. **Never record a team verdict on behalf of the user** unless the user explicitly provides the role name, verdict, and reason.
3. **`override` is a last resort.** If the user requests it without a reason, ask for one first.
4. **Synthesis does not advance the stage.** After any synthesis command, the gate remains at its current status. The user must call `approve` (after team verdicts) to advance.

---

## Stage flow reference

```
discuss           → capability-synth → [team-verdict ×3] → approve
capability-review → design-synth     → [team-verdict ×3] → approve
epic-shaping      → story-synth      → [team-verdict ×3] → approve
story-slicing     → story-enrichment-synth → [team-verdict ×3] → approve
story-enrichment  → openspec-synth   → [team-verdict ×3] → approve
spec-authoring    → implementation-plan-synth → [team-verdict ×3] → approve
implementation-planning → next (no gate)
implementation    → ci-feedback / execution-path / drift-check / verify-fix → next
review            → feedback-synth   → [team-verdict ×3] → approve
release-planning  → [team-verdict ×3] → approve → done
```

Security Reviewer activates automatically (Python decides) when:
- Story contains security signals (auth, crypto, secrets, injection)
- path_classifier returns `flagged`  
- 3 or more interface signals detected

When Security Reviewer is active, verdicts required = 4 (not 3).

---

## Startup — new workflow

When the user starts a new workflow without specifying a command:

1. Call `actions` to check if a workflow already exists
2. If new: suggest `shapeitup:capability-synth` and ask for the epic name/description
3. If existing: render current state and available commands
4. Do not synthesise anything until the user confirms

---

## Error handling

| Error | What to do |
|-------|-----------|
| `Command '<cmd>' is not allowed in stage '<stage>'` | Show allowed commands for this stage, suggest the right one |
| `Waiting for review from: <roles>` | List pending roles, tell user to run `shapeitup:team-verdict --role <role> --verdict approve` |
| `ok: false` with any other message | Surface the full message, do not attempt recovery without user input |
| File not found (design artifact missing) | Suggest the synthesis command that creates it |

