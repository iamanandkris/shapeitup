"""
_base.py
--------
Base types for shapeitup command handlers.

Every command handler receives a CommandContext and returns a CommandResult.
The dispatcher owns state load/save and gate enforcement.
Handlers only contain command-specific logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapeitup.core.state import WorkflowState
from shapeitup.core.team import ActiveTeam


@dataclass
class CommandContext:
    slug: str
    root: Path
    reason: str = ""
    items: str = ""
    role: str = ""
    verdict: str = ""
    findings: str = ""
    design_file: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def workflow_dir(self) -> Path:
        return self.root / ".workflow" / self.slug


@dataclass
class CommandResult:
    state: WorkflowState
    message: str = ""
    ml_outputs: dict[str, Any] = field(default_factory=dict)   # ML classifier results
    needs_llm: bool = False       # True = caller should invoke LLM synthesis
    llm_task: str = ""            # what the LLM should do (if needs_llm)
    artifacts_written: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Handler protocol ───────────────────────────────────────────────────────────

CommandHandler = "Callable[[WorkflowState, CommandContext, ActiveTeam | None], CommandResult]"
