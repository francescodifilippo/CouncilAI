"""The universal adapter: any CLI with a TTY.

This is the lowest common denominator and therefore the default. The session
is opened once and stays open, so the CLI keeps its own conversation history
and compacts its own context — which is a benefit, not a problem to manage.

What it costs, and why the code below looks the way it does:

* end-of-response has to be inferred from a sentinel token, and a model may
  omit it, repeat it, or emit it *inside* its own text. So we take the **last**
  occurrence, not the first, and the timeout — not the token — is the actual
  guarantee that a turn ends (§12).
* the process is the memory. If it dies, the participant is lobotomised, and
  ``memory_lost`` exists so that fact reaches the transcript instead of quietly
  degrading the debate.
"""

from __future__ import annotations

import contextlib
import re

from ..prompts import END_OF_RESPONSE_TOKEN, normalise, strip_ansi
from .base import ParticipantAdapter, TurnResult


class PexpectAdapter(ParticipantAdapter):
    needs_end_token = True

    def __init__(
        self,
        command: str,
        *,
        ready_pattern: str = r"> ",
        reply_timeout_s: float = 180.0,
        startup_timeout_s: float = 60.0,
        encoding: str = "utf-8",
    ) -> None:
        self.command = command
        self.ready_pattern = ready_pattern
        self.reply_timeout_s = reply_timeout_s
        self.startup_timeout_s = startup_timeout_s
        self.encoding = encoding
        self._child = None
        self._memory_lost = False

    # -- lifecycle ---------------------------------------------------------

    def open(self, system_prompt: str) -> None:
        import pexpect  # imported here so the API adapter works without a TTY

        had_session = self._child is not None
        self._child = pexpect.spawn(
            self.command,
            encoding=self.encoding,
            codec_errors="replace",
            timeout=self.startup_timeout_s,
            dimensions=(50, 200),
            echo=False,
        )
        # A reopened pexpect session is a *new* conversation: whatever the
        # participant remembered is gone. Declare it rather than hide it.
        self._memory_lost = had_session

        # Not fatal if it never matches: some CLIs print no recognisable prompt
        # before first input. The reply timeout catches a genuinely dead process.
        with contextlib.suppress(Exception):
            self._child.expect(self.ready_pattern, timeout=self.startup_timeout_s)

        self._child.sendline(system_prompt.replace("\n", " "))
        with contextlib.suppress(Exception):
            self._child.expect(self.ready_pattern, timeout=self.startup_timeout_s)

    def close(self) -> None:
        if self._child is not None:
            try:
                self._child.close(force=True)
            finally:
                self._child = None

    @property
    def memory_lost(self) -> bool:
        return self._memory_lost

    # -- turn --------------------------------------------------------------

    def send_turn(self, text: str) -> TurnResult:
        import pexpect

        if self._child is None:
            raise RuntimeError("open() must be called before send_turn()")

        # Discard whatever the CLI printed after the previous reply — its
        # prompt, spinners, status lines — so that `before` contains this
        # turn's answer and nothing else.
        self._drain()
        if self._child is None:
            raise RuntimeError("CLI process exited between turns")

        # Multi-line input through a TTY is where interactive parsers bite.
        # prompts.sanitise_contribution() has already prefixed every line with
        # a space so none can begin with a command character; collapsing to a
        # single line here removes the remaining ambiguity.
        payload = " ".join(line.strip() for line in text.splitlines() if line.strip())
        try:
            self._child.sendline(payload)
        except (pexpect.EOF, OSError) as exc:
            self._memory_lost = True
            self._child = None
            raise RuntimeError("CLI process exited before receiving the turn") from exc

        truncated = False
        try:
            self._child.expect(re.escape(END_OF_RESPONSE_TOKEN), timeout=self.reply_timeout_s)
            # Keep reading briefly through the next prompt. If the model
            # quoted the token and later emitted the real one, _clean() can
            # then split on the last occurrence instead of truncating early.
            raw = (self._child.before or "") + END_OF_RESPONSE_TOKEN
            try:
                self._child.expect(self.ready_pattern, timeout=2)
                raw += self._child.before or ""
            except pexpect.TIMEOUT:
                raw += self._child.before or ""
            except pexpect.EOF:
                raw += self._child.before or ""
                self._memory_lost = True
                self._child = None
        except pexpect.TIMEOUT:
            # The sentinel is best-effort; the timeout is the guarantee.
            raw = self._child.before or ""
            truncated = True
        except pexpect.EOF:
            raw = self._child.before or ""
            truncated = True
            self._memory_lost = True
            self._child = None

        return TurnResult(text=self._clean(raw, self.ready_pattern), truncated=truncated)

    # -- helpers -----------------------------------------------------------

    def _drain(self) -> None:
        """Read and discard anything already buffered from the CLI."""
        import pexpect

        if self._child is None:
            return
        while True:
            try:
                if not self._child.read_nonblocking(size=4096, timeout=0):
                    break
            except pexpect.TIMEOUT:
                break
            except (pexpect.EOF, OSError, ValueError):
                self._memory_lost = True
                self._child = None
                break

    @staticmethod
    def _clean(raw: str, ready_pattern: str = "") -> str:
        text = strip_ansi(normalise(raw or ""))
        # The last sentinel ends the response; earlier occurrences may have
        # been quoted as ordinary text and are removed below.
        if END_OF_RESPONSE_TOKEN in text:
            text = text.rsplit(END_OF_RESPONSE_TOKEN, 1)[0]
        text = text.replace(END_OF_RESPONSE_TOKEN, "")

        lines = [ln.rstrip() for ln in text.replace("\r", "").splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        # A prompt may still lead the capture if the CLI printed it before the
        # answer. Strip one leading match; belt and braces with the expect()
        # above, because prompt behaviour differs from client to client.
        if lines and ready_pattern:
            lines[0] = re.sub(rf"^(?:{ready_pattern})+", "", lines[0]).lstrip()
            if not lines[0]:
                lines.pop(0)

        return "\n".join(lines).strip()
