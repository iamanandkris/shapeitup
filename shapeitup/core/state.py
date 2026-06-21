"""
state.py
--------
Typed state model for shapeitup workflow lanes.

Improvements over wrkflw's workflow_state.py:
  - Strict enum types for stage and gate_status — no freeform strings accepted
  - atomic JSON write (tmp → rename) preserved
  - schema_version for explicit future migrations
  - from_text() parses legacy state.md format for backward compat
  - Integrates with team.py: stores active team signals alongside state
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Canonical stage values ─────────────────────────────────────────────────────

class Stage(str, Enum):
    DISCUSS              = "discuss"
    CAPABILITY_REVIEW    = "capability-review"
    EPIC_SHAPING         = "epic-shaping"
    STORY_SLICING        = "story-slicing"
    STORY_ENRICHMENT     = "story-enrichment"
    SPEC_AUTHORING       = "spec-authoring"
    IMPLEMENTATION_PLANNING = "implementation-planning"
    IMPLEMENTATION       = "implementation"
    REVIEW               = "review"
    RELEASE_PLANNING     = "release-planning"
    DONE                 = "done"

    @classmethod
    def _missing_(cls, value: object) -> "Stage | None":
        # case-insensitive lookup
        for member in cls:
            if member.value == str(value).lower():
                return member
        return None


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.DISCUSS,
    Stage.CAPABILITY_REVIEW,
    Stage.EPIC_SHAPING,
    Stage.STORY_SLICING,
    Stage.STORY_ENRICHMENT,
    Stage.SPEC_AUTHORING,
    Stage.IMPLEMENTATION_PLANNING,
    Stage.IMPLEMENTATION,
    Stage.REVIEW,
    Stage.RELEASE_PLANNING,
    Stage.DONE,
)

GATED_STAGES: frozenset[Stage] = frozenset({
    Stage.CAPABILITY_REVIEW,
    Stage.EPIC_SHAPING,
    Stage.STORY_SLICING,
    Stage.STORY_ENRICHMENT,
    Stage.SPEC_AUTHORING,
    Stage.REVIEW,
    Stage.RELEASE_PLANNING,
})


class GateStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    BLOCKED  = "blocked"
    REJECTED = "rejected"

    @classmethod
    def _missing_(cls, value: object) -> "GateStatus | None":
        for member in cls:
            if member.value == str(value).lower():
                return member
        return None


# ── Routing tables (source of truth — not in any markdown file) ───────────────

APPROVAL_NEXT_STAGE: dict[Stage, Stage] = {
    Stage.DISCUSS:                  Stage.CAPABILITY_REVIEW,
    Stage.CAPABILITY_REVIEW:        Stage.EPIC_SHAPING,
    Stage.EPIC_SHAPING:             Stage.STORY_SLICING,
    Stage.STORY_SLICING:            Stage.STORY_ENRICHMENT,
    Stage.STORY_ENRICHMENT:         Stage.SPEC_AUTHORING,
    Stage.SPEC_AUTHORING:           Stage.IMPLEMENTATION_PLANNING,
    Stage.IMPLEMENTATION_PLANNING:  Stage.IMPLEMENTATION,
    Stage.IMPLEMENTATION:           Stage.REVIEW,
    Stage.REVIEW:                   Stage.RELEASE_PLANNING,
    Stage.RELEASE_PLANNING:         Stage.DONE,
    Stage.DONE:                     Stage.DONE,
}

REWORK_TARGET: dict[Stage, Stage] = {
    Stage.CAPABILITY_REVIEW:     Stage.CAPABILITY_REVIEW,
    Stage.EPIC_SHAPING:          Stage.EPIC_SHAPING,
    Stage.STORY_SLICING:         Stage.STORY_SLICING,
    Stage.STORY_ENRICHMENT:      Stage.STORY_ENRICHMENT,
    Stage.SPEC_AUTHORING:        Stage.SPEC_AUTHORING,
    Stage.REVIEW:                Stage.IMPLEMENTATION_PLANNING,  # not back to implementation
    Stage.RELEASE_PLANNING:      Stage.RELEASE_PLANNING,
}

NEXT_ACTION_DEFAULTS: dict[Stage, str] = {
    Stage.DISCUSS:               "Inspect codebase, detect planning profile, build capability inventory, then shapeitup:next",
    Stage.CAPABILITY_REVIEW:     "Review capabilities.md — approve or refine before epic shaping",
    Stage.EPIC_SHAPING:          "Shape epics from capabilities, run design-synth, then approve or refine",
    Stage.STORY_SLICING:         "Slice stories from capabilities, run story-synth, then approve or refine",
    Stage.STORY_ENRICHMENT:      "Enrich stories with ACs and context, run story-enrichment-synth, then approve",
    Stage.SPEC_AUTHORING:        "Author spec for active story, run openspec-synth, then approve",
    Stage.IMPLEMENTATION_PLANNING: "Generate implementation plan, assign roles, then shapeitup:next",
    Stage.IMPLEMENTATION:        "Implement active story, run team-run, then shapeitup:next when done",
    Stage.REVIEW:                "Run feedback-synth, verify-fix, ci-feedback — then approve or reject",
    Stage.RELEASE_PLANNING:      "Assess production readiness, write release-plan.md, then approve",
    Stage.DONE:                  "Workflow complete. Archive artifacts or start next epic.",
}


# ── State dataclass ────────────────────────────────────────────────────────────

@dataclass
class WorkflowState:
    schema_version: int = 1
    slug: str = ""
    current_stage: Stage = Stage.DISCUSS
    gate_status: GateStatus = GateStatus.PENDING
    blocked_reason: str = ""
    rework_target: str = ""
    rejection_reason: str = ""
    approval_note: str = ""
    active_items: str = ""
    deferred_items: str = ""
    item_note: str = ""
    challenge_note: str = ""
    next_action: str = ""
    updated_at: str = ""

    # ML-derived signals stored alongside state (never read from markdown)
    story_signals: dict[str, Any] = field(default_factory=dict)

    # Team verdicts persisted so they survive across CLI calls
    team_verdicts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.next_action:
            self.next_action = NEXT_ACTION_DEFAULTS.get(self.current_stage, "")

    # ── Stage helpers ──────────────────────────────────────────────────────────

    @property
    def is_gated(self) -> bool:
        return self.current_stage in GATED_STAGES

    @property
    def can_advance_on_approve(self) -> bool:
        return self.current_stage in APPROVAL_NEXT_STAGE

    def next_stage_on_approve(self) -> Stage:
        return APPROVAL_NEXT_STAGE.get(self.current_stage, self.current_stage)

    def rework_stage_on_reject(self) -> Stage:
        return REWORK_TARGET.get(self.current_stage, self.current_stage)

    # ── Transitions ────────────────────────────────────────────────────────────

    def apply_approve(self, note: str = "") -> "WorkflowState":
        next_stage = self.next_stage_on_approve()
        self.approval_note = note
        self.current_stage = next_stage
        self.gate_status = GateStatus.PENDING if next_stage in GATED_STAGES else GateStatus.APPROVED
        self.next_action = NEXT_ACTION_DEFAULTS.get(next_stage, "")
        self.blocked_reason = ""
        self.rejection_reason = ""
        self._touch()
        return self

    def apply_reject(self, reason: str = "") -> "WorkflowState":
        rework = self.rework_stage_on_reject()
        self.rejection_reason = reason
        self.rework_target = rework.value
        self.current_stage = rework
        self.gate_status = GateStatus.REJECTED
        self.next_action = f"Address rejection: {reason}" if reason else NEXT_ACTION_DEFAULTS.get(rework, "")
        self._touch()
        return self

    def apply_next(self) -> "WorkflowState":
        """Advance non-gated stages."""
        if self.is_gated and self.gate_status != GateStatus.APPROVED:
            raise ValueError(
                f"Stage '{self.current_stage.value}' is gated — "
                f"use approve/reject, not next. Gate status: {self.gate_status.value}"
            )
        next_stage = self.next_stage_on_approve()
        self.current_stage = next_stage
        self.gate_status = GateStatus.PENDING if next_stage in GATED_STAGES else GateStatus.APPROVED
        self.next_action = NEXT_ACTION_DEFAULTS.get(next_stage, "")
        self._touch()
        return self

    def apply_block(self, reason: str) -> "WorkflowState":
        self.gate_status = GateStatus.BLOCKED
        self.blocked_reason = reason
        self._touch()
        return self

    def _touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # ── Serialisation ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["current_stage"] = self.current_stage.value
        d["gate_status"] = self.gate_status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = [
            f"- Slug: {self.slug}",
            f"- Current stage: {self.current_stage.value}",
            f"- Human gate status: {self.gate_status.value}",
        ]
        if self.blocked_reason:
            lines.append(f"- Blocked reason: {self.blocked_reason}")
        if self.rework_target:
            lines.append(f"- Rework target: {self.rework_target}")
        if self.rejection_reason:
            lines.append(f"- Rejection reason: {self.rejection_reason}")
        if self.approval_note:
            lines.append(f"- Approval note: {self.approval_note}")
        if self.active_items:
            lines.append(f"- Active items: {self.active_items}")
        if self.deferred_items:
            lines.append(f"- Deferred items: {self.deferred_items}")
        if self.item_note:
            lines.append(f"- Item note: {self.item_note}")
        if self.next_action:
            lines.append(f"- Next action: {self.next_action}")
        if self.updated_at:
            lines.append(f"- Updated at: {self.updated_at}")
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowState":
        stage = Stage(data.get("current_stage", "discuss"))
        gate = GateStatus(data.get("gate_status", "pending"))
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            slug=str(data.get("slug", "")),
            current_stage=stage,
            gate_status=gate,
            blocked_reason=str(data.get("blocked_reason", "")),
            rework_target=str(data.get("rework_target", "")),
            rejection_reason=str(data.get("rejection_reason", "")),
            approval_note=str(data.get("approval_note", "")),
            active_items=str(data.get("active_items", "")),
            deferred_items=str(data.get("deferred_items", "")),
            item_note=str(data.get("item_note", "")),
            challenge_note=str(data.get("challenge_note", "")),
            next_action=str(data.get("next_action", "")),
            updated_at=str(data.get("updated_at", "")),
            story_signals=dict(data.get("story_signals", {})),
            team_verdicts=dict(data.get("team_verdicts", {})),
        )

    @classmethod
    def from_markdown(cls, text: str) -> "WorkflowState":
        """Parse legacy state.md bullet format for backward compat."""
        import re
        state = cls()
        for line in text.splitlines():
            m = re.match(r"^-\s+([^:]+):\s*(.+)$", line.strip())
            if not m:
                continue
            key, val = m.group(1).strip(), m.group(2).strip()
            if key == "Slug":
                state.slug = val
            elif key == "Current stage":
                s = Stage(val)
                if s:
                    state.current_stage = s
            elif key == "Human gate status":
                g = GateStatus(val)
                if g:
                    state.gate_status = g
            elif key == "Blocked reason":
                state.blocked_reason = val
            elif key == "Rework target":
                state.rework_target = val
            elif key == "Rejection reason":
                state.rejection_reason = val
            elif key == "Approval note":
                state.approval_note = val
            elif key == "Active items":
                state.active_items = val
            elif key == "Deferred items":
                state.deferred_items = val
            elif key == "Item note":
                state.item_note = val
            elif key == "Next action":
                state.next_action = val
        return state

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, workflow_dir: Path) -> None:
        """Atomically write state.json; derive state.md from it."""
        workflow_dir.mkdir(parents=True, exist_ok=True)
        json_path = workflow_dir / "state.json"
        md_path = workflow_dir / "state.md"

        # Atomic write: tmp → rename
        fd, tmp_path = tempfile.mkstemp(dir=workflow_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self.to_json())
            os.replace(tmp_path, json_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Derived markdown render (never read back as source of truth)
        md_path.write_text(self.to_markdown(), encoding="utf-8")

    @classmethod
    def load(cls, workflow_dir: Path) -> "WorkflowState":
        """
        Load state from workflow_dir.
        Prefers state.json; falls back to state.md; returns blank default otherwise.
        """
        json_path = workflow_dir / "state.json"
        md_path = workflow_dir / "state.md"

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                return cls.from_dict(data)
            except Exception:
                pass

        if md_path.exists():
            try:
                return cls.from_markdown(md_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        return cls()
