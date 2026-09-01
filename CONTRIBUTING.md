# Contributing

The project is at the stage where **argument is worth more than code**. The
specification is further along than the implementation on purpose, and the
most useful contribution right now is telling me where the design is wrong.

## Before opening an issue about a design decision

Check [`SPECIFICATION.md` Appendix C](SPECIFICATION.md#21-appendix-c--outcome-of-the-technical-review-of-v3)
first. The design has already been through a three-round technical review, and
findings are recorded there as **accepted**, **downgraded** or **rejected**,
each with its reasoning. Several of the obvious objections are already in that
table — including a few that were rejected for reasons that are not obvious.

That is not a way of closing the conversation. If you think a rejection was
wrong, say so and say why: two of the best decisions in the current design came
out of a rejected finding being argued back.

The same applies to the changelog. §1.1 lists what was abandoned and why; §20.3
explains what changed between v3 and v4. If something looks like an odd choice,
the reason it is that way is usually written down somewhere.

## Kinds of contribution, in rough order of usefulness

1. **A design argument.** Especially about the product layer: turn policies,
   role prompts, phases, what makes a debate worth reading. That is the part
   with the least prior art and the most room to be wrong.
2. **A new `PexpectAdapter` for a client.** The fleet is heterogeneous by
   design. Every new adapter must pass the conformance checklist in
   [§14.5](SPECIFICATION.md#145-prompt-injection-and-context-assembly) — in
   particular the leading-command-character test, which behaves differently on
   every CLI.
3. **A role prompt.** Drop a `.txt` into `roles/`. A good one produces
   disagreement that is *useful* rather than merely contrarian.
4. **Code.** See the phase boundaries in §18; a change that belongs to Phase 2
   is not more welcome for arriving during Phase 0.

## Ground rules for code

- Python 3.11+, formatted and linted with `ruff` (`ruff check src tests`).
- Two runtime dependencies, and it should stay that way: `pexpect` and
  `PyYAML`. The API adapter uses `urllib` from the standard library on purpose
  — adding a model must never mean adding a dependency.
- Adding a client means writing an adapter, not touching `debate.py`. If your
  change needs the debate loop to know which transport is in use, something has
  gone wrong: transport is a per-seat property (§17.7).
- No participant gets privileges over another. Administrative capabilities go
  on the admin plane (§3.3.D), never into the debate protocol.
- Sanitisation belongs in the wrapper's context assembly, never in the Arbiter
  (§4.4). If you find yourself parsing debate text for commands, stop.

## Tests

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

The Phase 0 tests use a stub adapter, so they need no CLI, no network and no
API key. Keep it that way: a test that requires a subscription is a test nobody
will run.

## Security

Do not open a public issue for anything involving credentials, sandbox escape
from a participant CLI, or a way for one participant to reach another's
session. Contact me directly instead.

Note that some things are deliberately **out of scope** and are documented as
such in [§14.1](SPECIFICATION.md#141-threat-model): the host operator is
trusted, and so is whatever is baked into the wrapper image. A report that
assumes an attacker who can already write inside the wrapper is describing a
compromise that has happened upstream.
