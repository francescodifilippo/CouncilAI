"""OpenAI-compatible endpoints and multi-model gateways.

The simplest of the three adapters, and the one that covers the long tail:
any model reachable through an OpenAI-compatible ``/chat/completions`` route
becomes a seat without writing a new adapter.

The wrapper keeps the history here — trivial when turns are two paragraphs —
so a process restart does not lobotomise the participant. The subscription
argument does not apply to models you pay per token for anyway; for those, a
terminal to scrape buys nothing an HTTP call does not.

Uses ``urllib`` from the standard library on purpose: adding a model should
never mean adding a dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import ParticipantAdapter, TurnResult


class ApiAdapter(ParticipantAdapter):
    needs_end_token = False

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        max_output_tokens: int = 700,
        timeout_s: float = 180.0,
        temperature: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.temperature = temperature
        self._messages: list[dict[str, str]] = []

    def open(self, system_prompt: str) -> None:
        self._messages = [{"role": "system", "content": system_prompt}]

    def send_turn(self, text: str) -> TurnResult:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"environment variable {self.api_key_env} is not set; "
                f"the API adapter for model {self.model!r} cannot authenticate"
            )

        self._messages.append({"role": "user", "content": text})

        payload: dict[str, object] = {
            "model": self.model,
            "messages": self._messages,
            "max_tokens": self.max_output_tokens,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # surface the provider's message
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"{self.model}: HTTP {exc.code} — {detail}") from exc

        answer = body["choices"][0]["message"]["content"] or ""
        self._messages.append({"role": "assistant", "content": answer})

        usage = body.get("usage") or {}
        return TurnResult(
            text=answer.strip(),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
        )

    def close(self) -> None:
        self._messages = []
