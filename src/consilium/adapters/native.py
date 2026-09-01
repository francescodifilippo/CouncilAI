"""Non-interactive CLIs with process exit as the response boundary."""

from __future__ import annotations

import math
import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from .base import ParticipantAdapter, TurnResult

PROMPT = "{prompt}"


class NativeAdapter(ParticipantAdapter):
    """Run one headless CLI command per turn, resuming inside a private cwd."""

    needs_end_token = False

    def __init__(
        self,
        *,
        command: str | Sequence[str],
        resume_command: str | Sequence[str],
        timeout_s: float = 180.0,
        env: Mapping[str, str] | None = None,
        env_from: Mapping[str, str] | None = None,
    ) -> None:
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or timeout_s <= 0
            or not math.isfinite(timeout_s)
        ):
            raise ValueError("native timeout must be a positive number")
        self.command = self._argv(command)
        self.resume_command = self._argv(resume_command)
        self.timeout_s = timeout_s
        self.env = dict(env or {})
        self.env_from = dict(env_from or {})
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.env.items()
        ):
            raise ValueError("native env must map strings to strings")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.env_from.items()
        ):
            raise ValueError("native env_from must map environment names to names")
        self._system_prompt = ""
        self._started = False
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._memory_lost = False

    def open(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt
        self._memory_lost = False
        if self._tmp is None:
            # ponytail: resume-by-latest stays safe while each seat owns one cwd;
            # parse provider session IDs if a client stops scoping latest by cwd.
            self._tmp = tempfile.TemporaryDirectory(prefix="consilium-seat-")

    def send_turn(self, text: str) -> TurnResult:
        if self._tmp is None:
            raise RuntimeError("open() must be called before send_turn()")

        first = not self._started
        prompt = (
            "\n\n".join(part for part in (self._system_prompt, text) if part)
            if first
            else text
        )
        command = self.command if first else self.resume_command
        self._memory_lost = False

        try:
            answer = self._run(command, prompt)
        except RuntimeError:
            if not first:
                self._memory_lost = True
                self._started = False
            raise

        self._started = True
        return TurnResult(text=answer)

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
        self._started = False

    @property
    def memory_lost(self) -> bool:
        return self._memory_lost

    def _run(self, template: Sequence[str], prompt: str) -> str:
        argv = [prompt if arg == PROMPT else arg for arg in template]
        stdin = None if PROMPT in template else prompt
        env = os.environ.copy()
        env.update(self.env)
        for destination, source in self.env_from.items():
            value = os.environ.get(source)
            if value is None:
                raise RuntimeError(f"environment variable {source} is not set")
            env[destination] = value

        try:
            completed = subprocess.run(
                argv,
                cwd=Path(self._tmp.name),
                env=env,
                input=stdin,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"executable not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{argv[0]} timed out after {self.timeout_s:g}s") from exc

        if completed.returncode:
            detail = completed.stderr.strip()[-500:] or "no error output"
            raise RuntimeError(f"{argv[0]} exited {completed.returncode}: {detail}")
        return completed.stdout.strip()

    @staticmethod
    def _argv(command: str | Sequence[str]) -> list[str]:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv or not isinstance(argv[0], str) or not argv[0] or not all(
            isinstance(arg, str) for arg in argv
        ):
            raise ValueError("native command must be a non-empty argv")
        return argv
