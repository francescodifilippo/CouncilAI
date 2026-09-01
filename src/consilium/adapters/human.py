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

import contextlib
import hmac
import html
import math
import queue
import secrets
import select
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

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
    reports_token_usage = True

    def __init__(self, *, timeout_s: float | None = 30.0, prompt: str = "you> ") -> None:
        if timeout_s is not None and (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or timeout_s < 0
            or not math.isfinite(timeout_s)
        ):
            raise ValueError("human timeout must be a non-negative number or null")
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

        line = self._read_line(poll=self.away)
        if line is None:
            message = (
                "[away — turn skipped automatically]"
                if self.away
                else "[no input — turn skipped]"
            )
            print(message)
            return TurnResult(text="")

        return self._turn_result(line)

    # -- input -------------------------------------------------------------

    def _read_line(self, *, poll: bool = False) -> str | None:
        timeout_s = 0.0 if poll else self.timeout_s
        if timeout_s is None:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()
            return sys.stdin.readline()

        if not poll:
            sys.stdout.write(f"{self.prompt}({timeout_s:.0f}s, /help) ")
            sys.stdout.flush()
        ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
        if not ready:
            if not poll:
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

    def _turn_result(self, line: str) -> TurnResult:
        line = line.strip()
        if line.startswith("/"):
            self.pending_command = self._parse_command(line)
            return TurnResult(text="")
        return TurnResult(text=line)


class _WebServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _WebHandler(BaseHTTPRequestHandler):
    server: _WebServer

    def do_GET(self) -> None:
        adapter = self.server.adapter
        url = urlsplit(self.path)
        token = parse_qs(url.query).get("token", [""])[0]
        if url.path != "/" or not hmac.compare_digest(token, adapter._token):
            self._respond(404, "Not found")
            return
        self._respond(200, adapter._page(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        adapter = self.server.adapter
        if urlsplit(self.path).path != "/submit":
            self._respond(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, "Invalid content length")
            return
        if length < 0 or length > adapter.max_body_bytes:
            self._respond(413, "Submission too large")
            return

        form = parse_qs(
            self.rfile.read(length).decode("utf-8", errors="replace"),
            keep_blank_values=True,
        )
        token = form.get("token", [""])[0]
        if not hmac.compare_digest(token, adapter._token):
            self._respond(403, "Invalid form token")
            return
        line = (form.get("command") or form.get("text") or [""])[0]
        if not adapter._submit(line):
            self._respond(409, "No active turn or a submission is already queued")
            return

        self.send_response(303)
        self.send_header("Location", adapter.url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _respond(
        self,
        status: int,
        body: str,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)


class WebHumanAdapter(HumanAdapter):
    """A local-only HTML form for the human moderator."""

    max_body_bytes = 65_536

    def __init__(
        self,
        *,
        timeout_s: float | None = 30.0,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("the human web interface must bind to loopback")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
            raise ValueError("web port must be an integer between 0 and 65535")
        self.host = host
        self.port = port
        self.url = ""
        self._token = secrets.token_urlsafe(24)
        self._submissions: queue.Queue[str] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._context = "Waiting for the first turn."
        self._waiting = False
        self._server: _WebServer | None = None
        self._thread: threading.Thread | None = None

    def open(self, system_prompt: str) -> None:  # noqa: ARG002
        if self._server is not None:
            return
        server = _WebServer((self.host, self.port), _WebHandler)
        server.adapter = self
        self._server = server
        self.url = f"http://{self.host}:{server.server_port}/?token={quote(self._token)}"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[human web interface: {self.url}]")

    def send_turn(self, text: str) -> TurnResult:
        self.pending_command = None
        with self._lock:
            self._context = text
            self._waiting = True
        try:
            if self.away:
                line = self._submissions.get_nowait()
            elif self.timeout_s is None:
                line = self._submissions.get()
            else:
                line = self._submissions.get(timeout=self.timeout_s)
        except queue.Empty:
            return TurnResult(text="")
        finally:
            with self._lock:
                self._waiting = False
                with contextlib.suppress(queue.Empty):
                    self._submissions.get_nowait()
        return self._turn_result(line)

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def _submit(self, line: str) -> bool:
        with self._lock:
            if not self._waiting and not line.strip().startswith("/"):
                return False
            try:
                self._submissions.put_nowait(line)
            except queue.Full:
                return False
            return True

    def _page(self) -> str:
        with self._lock:
            context = html.escape(self._context)
            waiting = self._waiting
            state = "Your turn" if waiting else "Waiting for the council"
        refresh = "" if waiting else '<meta http-equiv="refresh" content="2">'
        token = html.escape(self._token, quote=True)
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>Consilium moderator</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 70rem;
        margin: 2rem auto; padding: 0 1rem; }}
pre, textarea {{ box-sizing: border-box; width: 100%; padding: 1rem; white-space: pre-wrap; }}
pre {{ background: #f4f4f4; }} textarea {{ min-height: 10rem; }}
.commands {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }}
button {{ padding: .6rem 1rem; }}
</style></head><body>
<main><h1>Consilium moderator</h1><p aria-live="polite"><strong>{state}</strong></p>
<h2>Current context</h2><pre>{context}</pre>
<form method="post" action="/submit">
<input type="hidden" name="token" value="{token}">
<label for="text"><strong>Your contribution</strong></label>
<textarea id="text" name="text" maxlength="{self.max_body_bytes}"></textarea>
<p><button type="submit">Send contribution</button></p>
<div class="commands" aria-label="Moderator commands">
<button name="command" value="/status">Status</button>
<button name="command" value="/away">Away</button>
<button name="command" value="/back">Back</button>
<button name="command" value="/end">End debate</button>
<button name="command" value="">Skip turn</button>
</div></form></main></body></html>"""
