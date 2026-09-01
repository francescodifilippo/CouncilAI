"""The Phase 0 debate loop.

Everything in one process: no Arbiter, no sockets, no TLS, no containers.
What this file *does* implement faithfully is the part Phase 0 exists to test
(SPECIFICATION.md §18):

* the adapter boundary, so transport stays a per-seat property;
* assigned roles, without which models converge toward polite agreement;
* delta-only context — each seat receives what happened since its own last
  turn, never the accumulated history;
* cold entry, so a seat joining late judges without inherited preconceptions;
* a transcript that makes two debates comparable;
* a cost cap, because an unattended debate must not run forever.

The question this phase answers is not "does it work" but "are the debates
worth reading". If they are not, the project should stop here.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import (
    HumanAdapter,
    ModeratorCommand,
    ParticipantAdapter,
    build_adapter,
    load_role_prompt,
)
from .prompts import Contribution, assemble_context, build_system_prompt
from .status import Cap, Status, repetition_index
from .transcript import Transcript, role_prompt_hash
from .wrappers import resolve_wrapper

PHASES = ("OPENING", "REBUTTAL", "CLOSING")
TURN_POLICIES = ("round_robin", "least_recently_spoken")


def _validate_config(config: dict[str, Any]) -> None:
    topic = config.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("'topic' must be a non-empty string")

    participants = config.get("participants")
    if not isinstance(participants, list) or not participants:
        raise ValueError("'participants' must be a non-empty list")
    names: list[str] = []
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            raise ValueError(f"participant {index} must be a mapping")
        name = participant.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"participant {index} needs a non-empty 'name'")
        names.append(name.strip())
    if len(names) != len(set(names)):
        raise ValueError("participant names must be unique")

    phase = str(config.get("phase", "OPENING")).upper()
    if phase not in PHASES:
        raise ValueError(f"'phase' must be one of {', '.join(PHASES)}")
    policy = str(config.get("turn_policy", "round_robin")).lower()
    if policy not in TURN_POLICIES:
        raise ValueError(f"'turn_policy' must be one of {', '.join(TURN_POLICIES)}")
    if "role_visible" in config and not isinstance(config["role_visible"], bool):
        raise ValueError("'role_visible' must be true or false")

    cap = config.get("cap")
    if not isinstance(cap, dict):
        raise ValueError("'cap' must define at least one positive limit")
    limits = {name: cap.get(name) for name in ("max_rounds", "max_tokens", "max_seconds")}
    if all(value is None for value in limits.values()):
        raise ValueError("'cap' must define max_rounds, max_tokens or max_seconds")
    for name, value in limits.items():
        expected = (int, float) if name == "max_seconds" else int
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, expected)
            or value <= 0
            or (name == "max_seconds" and not math.isfinite(value))
        ):
            raise ValueError(f"cap.{name} must be a positive number")


@dataclass
class Seat:
    """One participant: model, effort, and a point of view (§5.3)."""

    name: str
    adapter: ParticipantAdapter
    role_prompt: str = ""
    role_label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    opened: bool = False
    joined_cold: bool = False
    #: Index into the debate's contribution log; everything after it is this
    #: seat's delta.
    seen_upto: int = 0
    own_turns: list[str] = field(default_factory=list)
    last_spoke_at: int = -1
    last_selected_at: int = -1

    @property
    def is_human(self) -> bool:
        return isinstance(self.adapter, HumanAdapter)

    def descriptor(self) -> dict[str, Any]:
        return {
            **self.meta,
            "adapter": type(self.adapter).__name__,
            "role_label": self.role_label,
            "role_prompt_hash": role_prompt_hash(self.role_prompt),
        }


class Debate:
    def __init__(self, config: dict[str, Any], *, root: Path) -> None:
        _validate_config(config)
        self.root = root
        self.topic: str = config["topic"].strip()
        self.role_visible: bool = bool(config.get("role_visible", True))
        self.turn_policy: str = str(config.get("turn_policy", "round_robin")).lower()
        self.phase: str = str(config.get("phase", "OPENING")).upper()

        cap_cfg = config.get("cap") or {}
        self.cap = Cap(
            max_rounds=cap_cfg.get("max_rounds"),
            max_tokens=cap_cfg.get("max_tokens"),
            max_seconds=cap_cfg.get("max_seconds"),
        )

        self.seats: list[Seat] = [self._build_seat(p) for p in config["participants"]]
        if (
            self.cap.max_tokens is not None
            and self.cap.max_rounds is None
            and self.cap.max_seconds is None
            and not all(seat.adapter.reports_token_usage for seat in self.seats)
        ):
            raise ValueError(
                "cap.max_tokens cannot be the only limit when an adapter does not "
                "report token usage; add cap.max_rounds or cap.max_seconds"
            )

        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
        out_dir = root / config.get("transcript_dir", "transcripts")
        self.transcript = Transcript(
            out_dir / f"debate-{stamp}.jsonl",
            topic=self.topic,
            role_visible=self.role_visible,
        )

        self.log: list[tuple[str, Contribution]] = []  # (seat name, contribution)
        self.turn_counter = 0
        self.state = "RUNNING"
        self.outcome: str | None = None
        self._cursor = 0

    # -- setup -------------------------------------------------------------

    def _build_seat(self, spec: dict[str, Any]) -> Seat:
        spec = resolve_wrapper(spec)
        return Seat(
            name=spec["name"].strip(),
            adapter=build_adapter(spec, root=self.root),
            role_prompt=load_role_prompt(spec, root=self.root),
            role_label=spec.get("role_label"),
            meta=dict(spec.get("meta") or {}),
        )

    def _ensure_open(self, seat: Seat) -> None:
        if seat.opened:
            return
        prompt = build_system_prompt(
            seat.role_prompt, needs_token=seat.adapter.needs_end_token
        )
        seat.adapter.open(prompt)
        seat.opened = True
        if seat.adapter.memory_lost:
            self._memory_lost(seat)

    def _memory_lost(self, seat: Seat) -> None:
        """A restarted seat rejoins with an empty memory (§5.5).

        Indistinguishable from a new arrival unless it is declared — so declare
        it, rather than let the moderator wonder why the answers got worse.
        """
        seat.seen_upto = len(self.log)
        seat.joined_cold = True
        self.transcript.event("PARTICIPANT_MEMORY_LOST", participant_id=seat.name)
        print(f"[!] {seat.name} lost its session memory and rejoins as a new arrival")

    # -- turn order --------------------------------------------------------

    def _next_seat(self) -> Seat:
        if self.turn_policy == "least_recently_spoken":
            seat = min(self.seats, key=lambda s: s.last_selected_at)
        else:
            seat = self.seats[self._cursor % len(self.seats)]
        seat.last_selected_at = self._cursor
        self._cursor += 1
        return seat

    def _round_boundary(self) -> bool:
        return self._cursor % max(len(self.seats), 1) == 0

    # -- context -----------------------------------------------------------

    def _delta_for(self, seat: Seat) -> list[Contribution]:
        """Everything said since this seat last spoke, excluding its own turns.

        A seat already remembers what it said — its CLI session or the API
        adapter's message list holds it. Re-sending it would only duplicate.
        """
        return [c for who, c in self.log[seat.seen_upto :] if who != seat.name]

    # -- running -----------------------------------------------------------

    def run(self) -> str:
        print(f"\nTOPIC: {self.topic}\n")
        print(f"Seats: {', '.join(self._describe(s) for s in self.seats)}")
        print(f"Cap: rounds={self.cap.max_rounds} tokens={self.cap.max_tokens} "
              f"seconds={self.cap.max_seconds}\n")

        try:
            while self.state == "RUNNING":
                if self._check_cap():
                    break
                seat = self._next_seat()
                self._take_turn(seat)

                if self.state != "RUNNING":
                    break
                if self._round_boundary():
                    self.cap.rounds += 1
                self._check_cap()
        except KeyboardInterrupt:
            self._end("FINISHED", reason="interrupted_by_moderator")
        finally:
            for seat in self.seats:
                seat.adapter.close()

        print(f"\nTranscript: {self.transcript.path}")
        return self.outcome or "ERROR"

    def _describe(self, seat: Seat) -> str:
        return f"{seat.name}[{seat.role_label}]" if seat.role_label else seat.name

    def _take_turn(self, seat: Seat) -> None:
        self.turn_counter += 1
        turn_id = self.turn_counter

        is_first = not seat.own_turns or seat.joined_cold
        context = assemble_context(
            topic=self.topic,
            phase=self.phase,
            delta=self._delta_for(seat),
            role_visible=self.role_visible,
            is_first_turn=is_first,
            joined_cold=seat.joined_cold,
        )

        if not seat.is_human:
            print(f"\n[turn {turn_id} — {self.phase}] {self._describe(seat)} …")

        try:
            self._ensure_open(seat)
            result = seat.adapter.send_turn(context)
        except Exception as exc:  # a broken seat must not kill the council
            self.transcript.event(
                "PARTICIPANT_ERROR", participant_id=seat.name, error=str(exc)
            )
            print(f"[!] {seat.name} failed this turn: {exc}")
            seat.seen_upto = len(self.log)
            if seat.adapter.memory_lost:
                seat.opened = False
                self._memory_lost(seat)
            return

        seat.seen_upto = len(self.log)
        seat.joined_cold = False
        if seat.adapter.memory_lost:
            seat.opened = False
            self._memory_lost(seat)
        self.cap.add_tokens(result.tokens_in, result.tokens_out)

        if isinstance(seat.adapter, HumanAdapter) and seat.adapter.pending_command:
            self._handle_command(seat, seat.adapter.pending_command)
            return

        if result.truncated:
            self.transcript.event("RESPONSE_TRUNCATED", participant_id=seat.name)

        text = result.text.strip()
        if not text:
            # An empty turn is a skip: no dedicated command, the mechanism is
            # simply a turn that produced nothing (§17.4).
            self.transcript.event("TURN_SKIPPED", participant_id=seat.name, turn_id=turn_id)
            return

        contribution = Contribution(who=seat.name, role_label=seat.role_label, text=text)
        self.log.append((seat.name, contribution))
        seat.own_turns.append(text)
        seat.last_spoke_at = turn_id

        self.transcript.turn(
            turn_id=turn_id,
            participant_id=seat.name,
            phase=self.phase,
            text=text,
            meta=seat.descriptor(),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )

        if not seat.is_human:
            print(text)

    # -- moderator steering ------------------------------------------------

    def _handle_command(self, seat: Seat, cmd: ModeratorCommand) -> None:
        if cmd.verb == "status":
            print(self.status().render())
        elif cmd.verb == "phase":
            new_phase = cmd.argument.upper()
            if new_phase in PHASES:
                self.phase = new_phase
                self.transcript.event("PHASE_CHANGED", detail_phase=new_phase)
                print(f"[phase → {new_phase}]")
            else:
                print(f"[unknown phase; one of {', '.join(PHASES)}]")
        elif cmd.verb == "inject":
            if cmd.argument:
                contribution = Contribution(
                    who="moderator", role_label="moderator", text=cmd.argument
                )
                self.log.append(("moderator", contribution))
                self.transcript.event(
                    "MODERATOR_STEERING", participant_id=seat.name, text=cmd.argument,
                    visible=True,
                )
                print("[injected]")
        elif cmd.verb in {"away", "back"}:
            self.transcript.event(
                "MODERATOR_AWAY" if cmd.verb == "away" else "MODERATOR_BACK",
                participant_id=seat.name,
            )
            print(f"[{cmd.verb}]")
            if cmd.verb == "back":
                print(self.status().render())
        elif cmd.verb == "end":
            self._end("FINISHED", reason="closed_by_moderator")
        else:
            print(f"[unknown command: /{cmd.verb}; use /help]")

    # -- status and ending -------------------------------------------------

    def status(self) -> Status:
        humans = [s for s in self.seats if s.is_human]
        return Status(
            debate_state=self.state,
            phase=self.phase,
            topic=self.topic,
            turn_counter=self.turn_counter,
            turns_since={
                s.name: (
                    self.turn_counter - s.last_spoke_at
                    if s.last_spoke_at >= 0
                    else self.turn_counter
                )
                for s in humans
            },
            cost={
                "tokens_total": self.cap.tokens_total,
                "cap": self.cap.max_tokens,
                "elapsed_s": self.cap.elapsed_s(),
            },
            repetition={
                s.name: repetition_index(s.own_turns[-1], s.own_turns[:-1])
                for s in self.seats
                if len(s.own_turns) >= 2
            },
        )

    def _end(self, outcome: str, **detail: Any) -> None:
        if self.state != "RUNNING":
            return
        self.state = outcome
        self.outcome = outcome
        self.transcript.ended(outcome, turn_counter=self.turn_counter, **detail)
        print(f"\n[debate ended: {outcome}" + (f" — {detail}" if detail else "") + "]")

    def _check_cap(self) -> bool:
        breached = self.cap.exceeded()
        if not breached:
            return False
        self.transcript.event("CAP_REACHED", cap=breached)
        self._end("STOPPED_BY_CAP", cap=breached)
        return True
