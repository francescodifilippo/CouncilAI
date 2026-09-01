"""Adapters: how a wrapper talks to its participant.

Transport belongs to the adapter, not to the architecture (SPECIFICATION.md
§17.7). Register a new one here and it becomes selectable from configuration
without touching the debate loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .api_adapter import ApiAdapter
from .base import ParticipantAdapter, TurnResult
from .human import HumanAdapter, ModeratorCommand
from .pexpect_adapter import PexpectAdapter

__all__ = [
    "ApiAdapter",
    "HumanAdapter",
    "ModeratorCommand",
    "ParticipantAdapter",
    "PexpectAdapter",
    "TurnResult",
    "build_adapter",
]


def build_adapter(spec: dict[str, Any], *, root: Path) -> ParticipantAdapter:
    """Instantiate the adapter named by a participant's configuration."""
    kind = str(spec.get("adapter", "pexpect")).lower()

    if kind == "human":
        return HumanAdapter(timeout_s=spec.get("timeout_s", 30.0))

    if kind == "pexpect":
        command = spec.get("command")
        if not command:
            raise ValueError(f"participant {spec.get('name')!r}: 'command' is required")
        return PexpectAdapter(
            command=command,
            ready_pattern=spec.get("ready_pattern", r"> "),
            reply_timeout_s=float(spec.get("reply_timeout_s", 180.0)),
        )

    if kind == "api":
        return ApiAdapter(
            base_url=spec["base_url"],
            model=spec["model"],
            api_key_env=spec.get("api_key_env", "OPENAI_API_KEY"),
            max_output_tokens=int(spec.get("max_output_tokens", 700)),
        )

    if kind == "native":
        raise NotImplementedError(
            "NativeAdapter arrives with Phase 1: it wraps clients that expose a "
            "non-interactive mode with session resume, so the participant's memory "
            "survives a process restart (SPECIFICATION.md §3.3.B, §5.5). "
            "Use adapter: pexpect until then."
        )

    raise ValueError(f"unknown adapter {kind!r}")


def load_role_prompt(spec: dict[str, Any], *, root: Path) -> str:
    """Read a seat's role_prompt, inline or from a file."""
    if spec.get("role_prompt"):
        return str(spec["role_prompt"])
    path = spec.get("role_prompt_file")
    if not path:
        return ""
    return (root / path).read_text(encoding="utf-8")
