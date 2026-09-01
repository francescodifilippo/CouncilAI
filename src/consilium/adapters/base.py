"""The adapter contract (SPECIFICATION.md §3.3.B).

Transport is a property of the *participant*, not of the architecture. The
Arbiter never learns which adapter a wrapper uses, and the protocol does not
change when the adapter does.

Three methods and one property. Everything a new client needs is here; adding
a brand should mean writing one subclass, not touching the debate loop.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class TurnResult:
    """What one turn produced.

    ``text`` is already cleaned of transport artifacts. ``tokens_in`` and
    ``tokens_out`` are best-effort: adapters that cannot know them report 0
    and the cost cap falls back on rounds and elapsed time.
    """

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    truncated: bool = False  # response taken on timeout, sentinel never arrived


class ParticipantAdapter(abc.ABC):
    """Interface between a wrapper and its participant."""

    #: Whether this transport needs the end-of-response sentinel in the prompt.
    needs_end_token: bool = False

    @abc.abstractmethod
    def open(self, system_prompt: str) -> None:
        """Start or prepare the session with the complete system prompt."""

    @abc.abstractmethod
    def send_turn(self, text: str) -> TurnResult:
        """Pass the turn content and return what the participant produced."""

    def close(self) -> None:  # noqa: B027 - optional hook, not every transport has a session
        """Close the session, if this transport has one."""

    @property
    def memory_lost(self) -> bool:
        """True if the previous session could not be recovered.

        The reverse side of cold entry (§5.5): a restarted participant rejoins
        under the same name with an empty memory. Unless it is declared, the
        moderator keeps addressing an interlocutor that no longer remembers.
        """
        return False
