"""The transcript: a first-class artifact, not a by-product of persistence.

It is the only way to read a debate, the surface a returning moderator lands
on, and the only thing that makes two debates comparable. Which is why the
metadata matters as much as the text: without brand, model, effort and role,
a transcript reread weeks later cannot even tell you which seat produced which
argument (SPECIFICATION.md §5.6).

Append-only JSONL. One record per turn or per event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def role_prompt_hash(role_prompt: str) -> str:
    """Identify a role without repeating its text on every line."""
    digest = hashlib.sha256(role_prompt.strip().encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


@dataclass
class TurnRecord:
    turn_id: int
    ts: str
    participant_id: str
    phase: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)
    kind: str = "turn"


@dataclass
class EventRecord:
    ts: str
    event: str
    participant_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    visible: bool = False
    kind: str = "event"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Transcript:
    """Append-only writer plus the header a moderator reads first."""

    def __init__(self, path: Path, *, topic: str, role_visible: bool) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []
        self._write(
            {
                "kind": "header",
                "ts": _now(),
                "topic": topic,
                "role_visible": role_visible,
                "spec_version": 4,
            }
        )

    # -- writing -----------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def turn(
        self,
        *,
        turn_id: int,
        participant_id: str,
        phase: str,
        text: str,
        meta: dict[str, Any],
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        self._write(
            asdict(
                TurnRecord(
                    turn_id=turn_id,
                    ts=_now(),
                    participant_id=participant_id,
                    phase=phase,
                    text=text,
                    meta=meta,
                    tokens={"in": tokens_in, "out": tokens_out},
                )
            )
        )

    def event(
        self,
        event: str,
        *,
        participant_id: str | None = None,
        visible: bool = False,
        **detail: Any,
    ) -> None:
        """Record a SYSTEM_EVENT (§5.5).

        ``visible: False`` is the normal case: the event belongs in the
        transcript but not in the participants' context. PARTICIPANT_MEMORY_LOST
        is the one that matters most — a restarted seat is indistinguishable
        from a new arrival unless somebody says so.
        """
        self._write(
            asdict(
                EventRecord(
                    ts=_now(),
                    event=event,
                    participant_id=participant_id,
                    detail=detail,
                    visible=visible,
                )
            )
        )

    def ended(self, outcome: str, **detail: Any) -> None:
        """``FINISHED`` and ``STOPPED_BY_CAP`` are different outcomes (§7.3)."""
        self._write({"kind": "end", "ts": _now(), "outcome": outcome, "detail": detail})

    # -- reading -----------------------------------------------------------

    def turns(self) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("kind") == "turn"]

    def render(self) -> str:
        """Plain-text rendering, for the synthesis pass and for humans."""
        lines: list[str] = []
        for r in self.records:
            if r.get("kind") != "turn" or not r.get("text"):
                continue
            role = r.get("meta", {}).get("role_label")
            who = f"{r['participant_id']} ({role})" if role else r["participant_id"]
            lines.append(f"[turn {r['turn_id']} — {r['phase']}] {who}:\n{r['text']}\n")
        return "\n".join(lines)
