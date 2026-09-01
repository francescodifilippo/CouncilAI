"""The human seat.

Phase 0 reads stdin with a ``select`` timeout, exactly as v1 did: press a key
and the countdown freezes; stay silent and the turn is skipped.

**Phase 0 shortcut, to be removed.** The specification is explicit that the
human uses local keybindings and never types commands into the dialogue
(§10, §14.3). There is no admin channel in Phase 0 and no terminal handler,
so a small set of slash commands stands in for the keybindings and for the
admin plane. They are intercepted here and never reach the debate content.
Phase 2 replaces them with `Ctrl+A`/`Ctrl+B`/`Ctrl+S` and the ADMIN channel.
"""

from __future__ import annotations

import select
import sys
from dataclasses import dataclass

from .base import ParticipantAdapter, TurnResult


@dataclass
class ModeratorCommand:
    """A steering action, not a contribution."""

    verb: str  # status | phase | inject | away | back | end
    argument: str = ""


HELP = """\
  /status            show turns elapsed, cost and repetition
  /phase <NAME>      OPENING | REBUTTAL | CLOSING
  /inject <text>     add an element to the discussion as a system note
  /away              stop being waited for; the debate continues without you
  /back              resume being waited for
  /end               close the debate (outcome: FINISHED)
  /help              this list
  (empty line)       skip your turn
"""


class HumanAdapter(ParticipantAdapter):
    needs_end_token = False

    def __init__(self, *, timeout_s: float | None = 30.0, prompt: str = "you> ") -> None:
        self.timeout_s = timeout_s
        self.prompt = prompt
        self.away = False
        #: Set when the last turn produced a command instead of a contribution.
        self.pending_command: ModeratorCommand | None = None

    def open(self, system_prompt: str) -> None:  # noqa: ARG002 - humans need no prompt
        return

    def send_turn(self, text: str) -> TurnResult:
        self.pending_command = None

        print("\n" + text + "\n")

        if self.away:
            print("[away — turn skipped automatically]")
            return TurnResult(text="")

        line = self._read_line()
        if line is None:
            print("[no input — turn skipped]")
            return TurnResult(text="")

        line = line.strip()
        if line.startswith("/"):
            self.pending_command = self._parse_command(line)
            return TurnResult(text="")

        return TurnResult(text=line)

    # -- input -------------------------------------------------------------

    def _read_line(self) -> str | None:
        if self.timeout_s is None:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()
            return sys.stdin.readline()

        sys.stdout.write(f"{self.prompt}({self.timeout_s:.0f}s, /help) ")
        sys.stdout.flush()
        ready, _, _ = select.select([sys.stdin], [], [], self.timeout_s)
        if not ready:
            print()
            return None
        # A key was pressed: the rest of the line is typed without a deadline,
        # which is the point of the non-blocking window.
        return sys.stdin.readline()

    def _parse_command(self, line: str) -> ModeratorCommand:
        verb, _, argument = line[1:].partition(" ")
        verb = verb.lower().strip()
        argument = argument.strip()

        if verb == "help":
            print(HELP)
            return ModeratorCommand("status")
        if verb == "away":
            self.away = True
        if verb == "back":
            self.away = False
        return ModeratorCommand(verb, argument)
