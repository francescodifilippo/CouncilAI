"""Cost cap and re-entry signals.

Two mechanisms, and both are deliberately *mechanical*. Consilium does not try
to detect that a debate has converged and does not score its quality: neither
is reliably computable, and a model's self-assessment of whether it said
anything new is not usable data. Termination is a human decision
(SPECIFICATION.md §5.7, §17.10).

What is automated:

* **the cap** — an unattended debate must not run forever, because nobody is
  watching the meter. This is the only mandatory automatic stop.
* **the re-entry signals** — a moderator who walked away comes back to a log
  that grew. Three numbers turn "reread everything" into "see where it got
  stuck". Repetition is similarity against a participant's *own* previous
  turns: it says where to intervene, never whether to stop.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class Cap:
    max_rounds: int | None = None
    max_tokens: int | None = None
    max_seconds: float | None = None

    started_at: float = field(default_factory=time.monotonic)
    rounds: int = 0
    tokens_total: int = 0

    def add_tokens(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_total += tokens_in + tokens_out

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def exceeded(self) -> str | None:
        """Return the name of the breached cap, or None."""
        if self.max_rounds is not None and self.rounds >= self.max_rounds:
            return "max_rounds"
        if self.max_tokens is not None and self.tokens_total >= self.max_tokens:
            return "max_tokens"
        if self.max_seconds is not None and self.elapsed_s() >= self.max_seconds:
            return "max_seconds"
        return None


def _normalise(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def repetition_index(latest: str, previous: list[str], *, window: int = 3) -> float:
    """How much a participant is repeating *itself*, in [0, 1].

    Compared only against that participant's own recent turns — this is not a
    measure of agreement with others, and not a convergence detector. A high
    value means the seat is spinning in place, which is exactly the thing a
    returning moderator wants to find without reading two hundred turns.
    """
    if not latest.strip() or not previous:
        return 0.0
    a = _normalise(latest)
    if not a:
        return 0.0
    scores = [
        SequenceMatcher(None, a, _normalise(p)).ratio()
        for p in previous[-window:]
        if p.strip()
    ]
    return round(max(scores), 3) if scores else 0.0


@dataclass
class Status:
    """What a returning moderator is shown (§5.7)."""

    debate_state: str
    phase: str
    topic: str
    turn_counter: int
    turns_since: dict[str, int]
    cost: dict[str, float | int | None]
    repetition: dict[str, float]

    def render(self) -> str:
        lines = [
            "",
            "── STATUS " + "─" * 58,
            f"  state    {self.debate_state}   phase {self.phase}   turn {self.turn_counter}",
            f"  topic    {self.topic[:70]}",
        ]
        if self.turns_since:
            since = ", ".join(f"{k}: {v}" for k, v in self.turns_since.items())
            lines.append(f"  absent   {since} turns since you last spoke")
        cap = self.cost.get("cap")
        tokens = self.cost.get("tokens_total", 0)
        elapsed = self.cost.get("elapsed_s", 0)
        cap_str = f" / {cap}" if cap else ""
        lines.append(f"  cost     {tokens}{cap_str} tokens, {elapsed:.0f}s elapsed")
        if self.repetition:
            worst = sorted(self.repetition.items(), key=lambda kv: -kv[1])
            lines.append("  looping  " + ", ".join(f"{k} {v:.2f}" for k, v in worst))
            top_name, top_val = worst[0]
            if top_val >= 0.75:
                lines.append(
                    f"           ↳ {top_name} is repeating itself; steer there"
                )
        lines.append("─" * 68)
        return "\n".join(lines)
