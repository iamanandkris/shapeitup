# shapeitup

shapeitup is a human-gated staged delivery workflow engine. When you run `shapeitup:run` in Claude Cowork, **real Claude agents do the work** — not just generate text for humans to review. Each role is an active agent.

---

## What the agents do

- **Product Owner agent**: Reviews business value, scope fit, AC-to-user-need mapping. Writes a review artifact. Verdict: approve / approve-with-changes / block.
- **Tech Lead agent**: Reviews architecture, boundaries, sequencing, integration risk. Verdict: approve / approve-with-changes / block.
- **QA Engineer agent**: Writes failing tests first (TDD), then validates after implementation. Verdict: approve / approve-with-changes / block.
- **Security Reviewer agent**: Activated when security signals are detected. Scans for auth, injection, data exposure. Blocks on any HIGH finding.
- **Implementer (pair programming)**: Two agents — Proposer and Challenger — discuss, challenge, reach consensus, then write code.

---

## Stage flow

```
discuss → capability-review → epic-shaping → story-slicing → story-enrichment
→ spec-authoring → implementation-planning → implementation → review → release-planning → done
```

Each stage (except implementation-planning and implementation) runs:

1. **Phase 1**: Synthesis agent generates the stage artifact (`capabilities.md`, `design-seed.md`, `stories.md`, etc.)
2. **Phase 2**: PO + TL + QA agents review in parallel (Security joins if activated)
3. **Gate check**: `review-sync` verifies all review artifacts exist. If the gate is clear, auto-advances to the next stage. No human needed for clean runs.

---

## Implementation stage (TDD + pair programming)

Implementation is special. Stories from `dag.json` are topologically sorted into parallel groups. Independent stories within a group run in parallel. Each story follows:

```
1. QA writes failing tests (must come first — TDD)
2. Pair programming: Proposer proposes → Challenger challenges (max 3 rounds) → Implementer writes code
3. Parallel validation: QA validates + Tech Lead reviews + Security scans
```

---

## When humans are needed (only 2 cases)

1. **Pair programming deadlock**: If Proposer and Challenger disagree after 3 rounds, shapeitup surfaces both positions to the user for a decision.
2. **Security HIGH finding**: Any HIGH-severity security finding stops auto-advance. Human must explicitly instruct next steps.

Optional: `shapeitup:run --checkpoints` pauses before every stage advance for human sign-off.

---

## Usage

### Prerequisites

- Python 3.10+
- Claude Cowork (desktop app) with the shapeitup skill installed

### Install

```bash
cd /path/to/your/project
pip install -e /path/to/shapeitup
```

### Start a workflow

In Claude Cowork:

```
shapeitup:run
```

Claude asks for a slug (e.g. `webhook-handler`) and feature description, then runs the full stage automatically.

---

## Commands reference

| Command | What it does |
|---|---|
| `shapeitup:run` | Run the full current stage (auto-advances if gate clears) |
| `shapeitup:run --checkpoints` | Same, but pause before each advance |
| `shapeitup:approve` | Manually advance stage |
| `shapeitup:actions` | Show current state and available commands |
| `shapeitup:override --reason "..."` | Force-advance past a blocked gate |
| `shapeitup:team-verdict --role product-owner --verdict approve` | Record a role verdict manually |
| `shapeitup:review-sync` | Check gate status |
| `shapeitup:stage-plan` | Get the phase plan for the current stage |
| `shapeitup:impl-schedule` | Get the TDD story schedule for implementation |
| `shapeitup:ci-feedback` | Classify CI failure (paste CI output) |
| `shapeitup:execution-path` | Analyze code change execution path |
| `shapeitup:drift-check` | Check for design drift |

### Python CLI (for advanced use)

```bash
python -m shapeitup.cli \
  --slug webhook-handler \
  --root /path/to/workflow \
  --command stage-plan \
  --output-format json
```

---

## Architecture

```
shapeitup/
├── shapeitup/
│   ├── cli.py                    # CLI dispatcher
│   └── core/
│       ├── state.py              # WorkflowState (persisted as state.json)
│       ├── team.py               # Role definitions + ActiveTeam + gate enforcement
│       ├── transitions.py        # State machine: allowed commands per stage
│       ├── stage_planner.py      # Phase-based parallel execution plan
│       ├── impl_scheduler.py     # DAG-driven TDD story groups
│       └── commands/
│           ├── agent_commands.py      # All active agent commands (reviews, pair programming)
│           ├── team_commands.py       # Team verdicts, review-sync, gate check
│           ├── synthesis_commands.py  # LLM synthesis stubs
│           └── impl_commands.py       # Implementation lifecycle commands
├── skills/shapeitup/SKILL.md     # Claude skill: parallel orchestrator
└── tests/
```

### Two-layer architecture

- **Python core**: Owns gate enforcement, team composition, stage routing, ML analysis. Never generates content.
- **SKILL.md (LLM layer)**: Owns content generation, agent spawning, pair programming orchestration. Never decides gates.

Gates are enforced by Python checking that artifact files exist on disk — not by trusting LLM-generated text.

---

## ML analysis modules (all local, no API)

- **Failure classifier**: Classifies CI failures by type and severity
- **Drift detector**: Detects design drift between implementation and spec
- **Execution path classifier**: Analyzes code change complexity and security signals
- **Semantic profile detector**: Identifies workflow type from feature description
- **Context relevance ranker**: Ranks context files by relevance to current stage

---

## Design principles

1. **Gates in code, not prompts.** Role verdicts and stage advancement are Python dataclasses and method calls. The LLM cannot decide "yes this is approved."
2. **Active agents, not text reviewers.** Each role runs as a real Claude agent that reads artifacts, reasons about them, and writes a verdict — it does not just generate text for a human to approve.
3. **TDD first.** QA writes failing tests before any implementation begins. The gate enforces this ordering.
4. **Human in the loop only when needed.** Two cases: pair programming deadlock, or a HIGH security finding. Everything else auto-advances.
5. **Atomic state.** `state.json` is written via tmp→rename. No partial writes.
6. **Audit trail.** `history.md` is append-only. Every command, its outcome, stage, and gate status are logged.
