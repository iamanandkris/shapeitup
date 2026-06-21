"""
_registry.py
------------
Command registry for shapeitup.

Commands register themselves with @register(). The dispatcher looks up
handlers by name. Unknown commands raise KeyError with suggestions.
"""
from __future__ import annotations

from typing import Callable
from shapeitup.core.state import WorkflowState
from shapeitup.core.team import ActiveTeam
from shapeitup.core.commands._base import CommandContext, CommandResult

HandlerFn = Callable[[WorkflowState, CommandContext, "ActiveTeam | None"], CommandResult]

REGISTRY: dict[str, HandlerFn] = {}


def register(*names: str) -> Callable[[HandlerFn], HandlerFn]:
    """Decorator to register a command handler under one or more names."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        for name in names:
            REGISTRY[name] = fn
        return fn
    return decorator


def lookup(command: str) -> HandlerFn:
    if command in REGISTRY:
        return REGISTRY[command]
    # Suggest close matches
    import difflib
    close = difflib.get_close_matches(command, REGISTRY.keys(), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise KeyError(f"Unknown command '{command}'.{hint}")
