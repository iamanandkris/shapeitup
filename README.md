# shapeitup

Human-gated staged delivery workflow engine with ML-powered analysis. Built for coding projects where you want structured progression through design, planning, implementation, and review — with local ML classifiers making deterministic decisions and an LLM layer that only synthesises content.

---

## Why shapeitup

Most AI-assisted dev tools let the LLM decide everything: what stage you're in, whether work is done, who should review it. shapeitup inverts that. The LLM writes content (designs, stories, plans, feedback). Python enforces everything structural.

| Concern | Owner | Never touches |
|---------|-------|---------------|
| Stage routing | Python state machine | LLM |
| Gate advancement | Python `check_gate()` | LLM |
| Team composition | ML classifiers | LLM |
| Role verdicts | Python + human input | LLM |
| CI failure classification | Regex + sklearn | LLM |
| Design/code drift detection | Sentence-transformer embeddings | LLM |
| Story path classification | Feature extraction + LR | LLM |
| Content generation (designs, stories, plans) | LLM skill | Python |

---

## Architecture

```
shapeitup/
├── shapeitup/
│   ├── cli.py                    # Main dispatcher — entry point
│   ├── core/
│   │   ├── state.py              # WorkflowState dataclass, stage enum, atomic save
│   │   ├── transitions.py        # STAGE_COMMANDS table, check_full_gate()
│   │   ├── team.py               # Role definitions, ActiveTeam, gate enforcement
│   │   └── commands/
│   │       ├── gate_commands.py      # approve, reject, next, override, reconcile, ...
│   │       ├── team_commands.py      # team-verdict, challenge, review-sync, records
│   │       ├── analysis_commands.py  # ci-feedback, execution-path, drift-check, dag-sync
│   │       ├── synthesis_commands.py # LLM-delegation stubs (capability-synth, ...)
│   │       └── impl_commands.py      # merge-gate, team-run, integration-gate, ...
│   └── ml/
│       ├── failure_classifier.py  # CI failure → 9 classes (regex + TF-IDF LR)
│       ├── drift_detector.py      # Design↔code drift (sentence-transformers / TF-IDF)
│       ├── path_classifier.py     # Story → simple | flagged (feature extraction + LR)
│       ├── profile_detector.py    # Profile matching (semantic embeddings)
│       └── context_ranker.py      # Context slot ranking (TF-IDF + static priority)
├── skills/
│   └── shapeitup/
│       └── SKILL.md              # Claude skill — LLM synthesis wrapper
└── tests/
    ├── test_cli.py               # 25 CLI integration tests
    ├── test_state.py             # 42 state machine tests
    ├── test_team.py              # 40 team enforcement tests
    ├── test_synthesis_commands.py # 29 synthesis/impl command tests
    └── ml/                       # ML module unit tests
```

---

## Installation

```bash
# Clone and install in development mode
git clone <repo> shapeitup
cd shapeitup
pip install -e ".[dev]"

# Verify
python -m pytest          # 173 tests, all should pass
shapeitup --help
```

Python 3.11+ required. ML dependencies (`sentence-transformers`, `scikit-learn`) are optional — all ML modules fall back gracefully to simpler algorithms when unavailable.

---

## Core concepts

### Workflow slug

Every workflow has a **slug** — a short identifier for the epic or feature being built. State lives at `<root>/.workflow/<slug>/`. Multiple slugs can coexist in the same repo.

```bash
shapeitup --slug payment-gateway --root /path/to/repo --command actions
```

### Stages

A workflow progresses through 11 stages in order:

```
discuss → capability-review → epic-shaping → story-slicing →
story-enrichment → spec-authoring → implementation-planning →
implementation → review → release-planning → done
```

### Gate enforcement

Every gated stage requires **all blocking roles** to record `approve` verdicts before `approve` advances the stage. This is enforced in Python — the LLM cannot bypass it.

### Team composition

The active team is computed by ML classifiers from story text, not from config:

| Role | Always active | Blocks gate |
|------|--------------|-------------|
| Product Owner | ✓ | ✓ |
| Tech Lead | ✓ | ✓ |
| Implementer | ✓ | ✗ |
| QA Engineer | ✓ | ✓ |
| Security Reviewer | When: security signal OR flagged OR ≥3 interface signals | ✓ |

---

## CLI usage

```bash
shapeitup \
  --slug  <slug>          # Workflow identifier
  --root  <path>          # Repo root (default: cwd)
  --command <cmd>         # Command to run
  [--reason  "..."]       # Free-text argument, story text, or log text
  [--role    "..."]       # Role name for team-verdict
  [--verdict "..."]       # approve | approve-with-changes | block
  [--findings "..."]      # Pipe-separated blocking findings
  [--items   "..."]       # Items for defer or rework-item
  [--output-format json]  # Machine-readable output
```

### Checking current state

```bash
shapeitup --slug my-epic --command actions
```

Output:
```
Stage      : discuss
Gate       : pending
Next action: Run capability-synth then submit team verdicts to advance
```

---

## Command reference

### Always available (any stage)

| Command | What it does |
|---------|-------------|
| `actions` | Show current stage, gate status, available commands |
| `override` | Force-advance past gate (requires `--reason`) |
| `reconcile` | Record a reconciliation note without changing stage |
| `drift-check` | ML: detect design↔code drift using embeddings |
| `dag-sync` | Build story dependency graph from `stories.md` |
| `team-verdict` | Record a role verdict (`--role`, `--verdict`, `--reason`) |
| `challenge` | Log a concern from a role |
| `memory-record` | Persist a memory note |
| `debt-record` | Log technical debt |
| `accounting-record` | Log an invocation entry |

### Gate commands

| Command | Allowed at |
|---------|-----------|
| `approve` | discuss, capability-review, epic-shaping, story-slicing, story-enrichment, spec-authoring, review, release-planning |
| `reject` | All stages with approve |
| `next` | discuss, implementation-planning, implementation, done |
| `refine` | implementation |
| `rework-item` | capability-review through spec-authoring |
| `proceed-only` | implementation-planning |
| `defer` | implementation-planning |
| `review-sync` | implementation, review |

### ML analysis commands

| Command | Stage | What it does |
|---------|-------|-------------|
| `ci-feedback` | implementation | Classify CI failure text → 9 classes (compilation_error, test_failure, type_error, lint_error, dependency_conflict, timeout, flaky, environment_error, unknown). Blocks state on HIGH non-retryable failures. |
| `execution-path` | implementation | Classify story as simple/flagged. Stores ML signals for team assembly. Activates Security Reviewer if security signals detected. |

### LLM synthesis commands (skill-delegated)

These return `needs_llm=True` — the Claude skill performs the actual generation.

| Command | Stage | Generates |
|---------|-------|-----------|
| `capability-synth` | discuss, capability-review | `capabilities.md` — in-scope, out-of-scope, open questions |
| `design-synth` | epic-shaping | `design-seed.md` — problem, approach, interfaces, risks |
| `story-synth` | story-slicing | `stories.md` — atomic stories with ACs, deps, test hints |
| `story-enrichment-synth` | story-enrichment | Enriches `stories.md` with edge cases, validation, cross-cutting concerns |
| `openspec-synth` | spec-authoring | `openspec.md` — API contracts, data models, auth |
| `openspec-sync` | spec-authoring | Diff-style update to `openspec.md` against current stories |
| `implementation-plan-synth` | spec-authoring, implementation-planning | `implementation-plan.md` — build order, parallel tracks, DoD |
| `feedback-synth` | any | `feedback-synthesis.md` — blocking items, notes, deferred, recommendation |
| `issue-advisor` | any | Direct advice: root cause, 3 options, recommendation |
| `replan` | implementation-planning, implementation | Appends replan section to `implementation-plan.md` |
| `verify-fix` | implementation | Direct advisory: does fix address root cause, what to test |
| `staff` | epic-shaping, story-slicing | `staffing-plan.md` — role assignments per story |

### Implementation-stage commands

| Command | What it does |
|---------|-------------|
| `team-run` | LLM: coordinate team assignments for a sprint |
| `team-run-level` | Set run scope: `story` \| `epic` \| `release` |
| `team-sync` | Snapshot team verdicts to `team-sync.json` |
| `merge-gate` | Check CI + drift fitness for merge (blocks on HIGH failures or drift) |
| `merge-apply` | Record a merge event to `merge-log.jsonl` |
| `integration-gate` | Validate DAG has no cycles/unknown deps; block if errors |
| `assign` | Record story→role assignment to `assignments.jsonl` |

---

## Typical workflow walkthrough

### 1. Start a new epic

```bash
# Check what's available
shapeitup --slug payments --command actions

# LLM generates capabilities.md (via Claude skill)
shapeitup --slug payments --command capability-synth --reason "Payment gateway with Stripe"

# Team reviews — each blocking role submits verdict
shapeitup --slug payments --command team-verdict --role product-owner --verdict approve --reason "scope is correct"
shapeitup --slug payments --command team-verdict --role tech-lead --verdict approve --reason "feasible with current stack"
shapeitup --slug payments --command team-verdict --role qa-engineer --verdict approve --reason "testable ACs"

# Gate passes — advance
shapeitup --slug payments --command approve --reason "capabilities agreed"
# Stage is now: capability-review
```

### 2. Design and story slicing

```bash
# Generate design seed
shapeitup --slug payments --command design-synth

# Repeat verdict + approve cycle through:
# capability-review → epic-shaping → story-slicing

# Slice stories
shapeitup --slug payments --command story-synth

# Validate dependency graph
shapeitup --slug payments --command dag-sync
```

### 3. Implementation

```bash
# Advance to implementation (non-gated)
shapeitup --slug payments --command next

# Classify story complexity (also sets team signals)
shapeitup --slug payments --command execution-path \
  --reason "Add Stripe webhook handler. AC: verify signature, parse event type, update order status."

# Check for drift during build
shapeitup --slug payments --command drift-check

# Submit CI failure for classification
shapeitup --slug payments --command ci-feedback \
  --reason "TypeError: Cannot read property 'id' of undefined at webhook.ts:42"

# Verify a fix
shapeitup --slug payments --command verify-fix \
  --reason "Added null check before accessing event.data.object.id"

# Check merge readiness
shapeitup --slug payments --command merge-gate
shapeitup --slug payments --command merge-apply --reason "PR #31 merged"

# Advance to review
shapeitup --slug payments --command next
```

### 4. Review and release

```bash
# Synthesise review feedback
shapeitup --slug payments --command feedback-synth

# Team verdicts + approve through review → release-planning → done
```

---

## Team verdict flow

At every gated stage, all blocking roles must approve before `approve` advances:

```bash
# Check who's still pending
shapeitup --slug payments --command review-sync

# Submit verdicts
shapeitup --slug payments --command team-verdict \
  --role product-owner --verdict approve --reason "LGTM"

shapeitup --slug payments --command team-verdict \
  --role tech-lead --verdict approve-with-changes \
  --reason "Add retry logic" \
  --findings "missing retry on 5xx|no timeout configured"

shapeitup --slug payments --command team-verdict \
  --role qa-engineer --verdict approve --reason "test coverage adequate"

# If Security Reviewer was activated by ML signals:
shapeitup --slug payments --command team-verdict \
  --role security-reviewer --verdict approve --reason "JWT validation correct"

# Gate clears — advance
shapeitup --slug payments --command approve
```

To **block** advancement:
```bash
shapeitup --slug payments --command team-verdict \
  --role tech-lead --verdict block \
  --findings "SQL injection risk in search query|no input sanitisation"
```

---

## State files

All state lives under `<root>/.workflow/<slug>/`:

| File | Written by | Purpose |
|------|-----------|---------|
| `state.json` | All commands | Authoritative state (stage, gate, verdicts, signals) |
| `state.md` | All commands | Human-readable mirror of state.json |
| `history.md` | All commands | Append-only audit log |
| `capabilities.md` | `capability-synth` | In-scope/out-of-scope capabilities |
| `design-seed.md` | `design-synth` | Design approach and interfaces |
| `stories.md` | `story-synth` | Story list with ACs and dependencies |
| `openspec.md` | `openspec-synth` | Interface contracts |
| `implementation-plan.md` | `implementation-plan-synth` | Build order and DoD |
| `ci-feedback.json` | `ci-feedback` | ML classification of last CI run |
| `ci-feedback.md` | `ci-feedback` | Human-readable CI analysis |
| `drift-check.json` | `drift-check` | Drift score and explanation |
| `execution-path.json` | `execution-path` | Story signals and path classification |
| `dag.json` | `dag-sync` | Story dependency graph |
| `team-sync.json` | `team-sync` | Snapshot of team verdict status |
| `review-log.md` | `challenge` | Running log of challenges and concerns |
| `assignments.jsonl` | `assign` | Story→role assignment log |
| `merge-log.jsonl` | `merge-apply` | Merge event log |
| `records/memory.jsonl` | `memory-record` | Memory notes |
| `records/debt.jsonl` | `debt-record` | Technical debt log |

---

## ML modules

All ML runs locally — no API key, no network calls.

### Failure classifier (`ci-feedback`)

Classifies CI failure text into 9 classes using regex patterns with TF-IDF + LogisticRegression fallback:

| Class | Severity | Retryable |
|-------|----------|-----------|
| `compilation_error` | high | no |
| `test_failure` | medium | no |
| `type_error` | high | no |
| `lint_error` | low | no |
| `dependency_conflict` | high | no |
| `timeout` | medium | yes |
| `flaky` | low | yes |
| `environment_error` | medium | yes |
| `unknown` | low | yes |

HIGH + non-retryable → blocks state until resolved.

### Drift detector (`drift-check`)

Computes cosine similarity between design artifacts and code snippets using sentence-transformers embeddings (falls back to TF-IDF). Returns:

| Type | Score range | Meaning |
|------|-------------|---------|
| `aligned` | ≥ 0.60 | Design and code are in sync |
| `design_ahead` | 0.35–0.60 | Design exists but code hasn't caught up |
| `code_ahead` | 0.35–0.60 | Code diverged from design |
| `significant_drift` | < 0.35 | Major misalignment — reconcile |

### Path classifier (`execution-path`)

Classifies stories as `simple` or `flagged` based on extracted features:

- `security_signal` → always flagged (activates Security Reviewer)
- word_count ≤ 80 and dep_count = 0 → always simple
- Otherwise: LogisticRegression on (word_count, ac_count, dep_count, interface_signals, complexity_score)

---

## Claude skill

The skill at `skills/shapeitup/SKILL.md` wraps the Python layer for use inside Claude. It:

- Routes all mechanical commands directly to the Python CLI
- Performs LLM synthesis for synthesis commands (generates the actual content)
- Never makes gate decisions — always delegates to Python
- Renders state in a consistent format after every command

Install as a Claude skill by zipping `skills/shapeitup/` with a `.skill` extension and installing via Settings → Capabilities.

---

## Running tests

```bash
# Full suite
python -m pytest

# Specific modules
python -m pytest tests/test_cli.py          # CLI integration
python -m pytest tests/test_state.py        # State machine
python -m pytest tests/test_team.py         # Team enforcement
python -m pytest tests/ml/                  # ML modules

# With coverage
python -m pytest --cov=shapeitup --cov-report=term-missing
```

Current: **173 tests, 0 failures**.

---

## Design principles

1. **Gates in code, not prompts.** Role verdicts and stage advancement are Python dataclasses and method calls. The LLM cannot decide "yes this is approved."

2. **ML for deterministic decisions.** Whether a story is security-sensitive, whether CI failed, whether design has drifted — these are classification tasks, not LLM judgment calls.

3. **LLM for generative tasks only.** The LLM writes capabilities, designs, stories, plans, and feedback. It never routes, gates, or structures.

4. **Graceful degradation.** Every ML module falls back (regex → TF-IDF → Jaccard) when heavyweight dependencies aren't installed. The tool works without sentence-transformers.

5. **Atomic state.** `state.json` is written via tmp→rename. No partial writes.

6. **Audit trail.** `history.md` is append-only. Every command, its outcome, stage, and gate status are logged.
