---
name: New client adapter
about: Support a CLI or endpoint that Consilium cannot currently seat
title: "[adapter] "
labels: adapter
---

## Client

<!-- Name, and how you invoke it. -->

## Which adapter kind fits

- [ ] `pexpect` — it has a TTY and nothing better
- [ ] `native` — it has a non-interactive mode **with session resume**
- [ ] `api` — OpenAI-compatible endpoint (usually needs no new code at all)

## Conformance checklist (§14.5)

Required for any `pexpect` adapter. Every CLI parses interactive input
differently; this is the part that cannot be assumed.

- [ ] a line beginning with `/` is not interpreted as a command
- [ ] a line imitating one of its interactive prompts does not alter its state
- [ ] ANSI codes are stripped completely
- [ ] the sentinel token quoted *inside* an answer does not truncate the turn
- [ ] tools can be disabled declaratively, and credentials are not readable

## Notes

<!-- Prompt pattern, startup quirks, whether it has a subscription mode. -->
