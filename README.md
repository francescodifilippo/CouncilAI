# Consilium

**A turn-based debate orchestrator for AI CLIs and humans.**

Consilium seats several AI participants — each running in its own commercial CLI, with its own persistent conversation — and one or more humans around a single table, and has them argue a topic in turns. A central Arbiter hands out turns; each participant keeps its own memory; a human moderator triggers the debate, steers it, and decides when it is over.

> **Status: design specification. No implementation yet.**
> This repository currently contains the architectural specification and its full evolutionary history. The code is not written. [`SPECIFICATION.md`](SPECIFICATION.md) is normative for the implementation work described in the roadmap below.

---

## Why this exists

Multi-agent orchestration frameworks already exist and solve turn-taking, shared context and speaker selection at the API level. Consilium is aimed at a narrower, different thing:

**Make the commercial agentic CLIs debate each other — each with its own persistent session and its own subscription — rather than calling APIs with keys.**

That constraint drives most of the design. It is why the wrapper drives a CLI instead of an SDK, why transport is pluggable rather than fixed, and why "preserve the local histories" is a first-class objective rather than an implementation detail.

The second thing Consilium is aimed at is the part orchestration frameworks do not address at all: **making the debate worth reading.** Three capable models given the same prompt and no assigned stance converge toward polite agreement within a few turns. Most of the product layer in this specification exists to prevent that.

---

## Core concepts

| Concept | What it is |
|---|---|
| **Arbiter** | Independent central hub. Owns the roster, hands out turns with deadlines, enforces the cost cap, persists the transcript. Knows nothing about brands, models or transports. |
| **Wrapper** | The trusted edge node, one per participant. Holds the credentials, talks the protocol, assembles context, and drives the participant through an adapter. The only component allowed on the control channel. |
| **Adapter** | How a wrapper talks to its participant. Three kinds: `PexpectAdapter` (any CLI with a TTY — the universal default), `NativeAdapter` (clients with a non-interactive mode and session resume), `ApiAdapter` (OpenAI-compatible endpoints). The Arbiter never knows which is in use. |
| **Seat** | A participant, defined by three independent dimensions: *model*, *effort level*, and **point of view** (`role_prompt`). |
| **Role prompt** | A per-participant prompt fragment — sceptic, pragmatist, devil's advocate — appended to the shared debate rules. The countermeasure to consensus collapse. |
| **Cold entry** | A participant joining mid-debate receives the topic and the current exchange, *never* the history — deliberately, so it judges without inherited preconceptions. Also the moderator's strongest steering move. |
| **Transcript** | Append-only JSONL on the Arbiter's volume, carrying model, effort and role metadata per turn. The only reading surface, and the thing that makes two debates comparable. |

---

## Architecture at a glance

```text
[ AI Wrapper 1 ] ──┬── CONTROL channel ──► [ Arbiter ]
                   └── DATA channel ─────► [ Arbiter ]

[ AI Wrapper 2 ] ──┬── CONTROL channel ──► [ Arbiter ]
                   └── DATA channel ─────► [ Arbiter ]

[ Human Wrapper ] ─┬── CONTROL channel ──► [ Arbiter ]
                   └── DATA channel ─────► [ Arbiter ]

[ Admin Console ] ──── ADMIN channel ────► [ Arbiter ]   (outside the roster)
```

Inside each wrapper:

```text
                              ┌── NativeAdapter    (non-interactive + resume)
[ Wrapper ] ── Adapter ───────┼── PexpectAdapter   (universal default, TTY)
                              └── ApiAdapter       (OpenAI-compatible endpoints)
```

**Two channels, one rule.** Commands travel on the control channel; debate content travels on the data channel. The two are physically separate, so a model that emits `UNREG:someone` into the conversation produces nothing but a strange sentence — the Arbiter has no path from dialogue to command, and therefore needs no semantic filter. Filtering happens in one place only: the wrapper's context assembly, where another participant's text becomes an agent's input.

A design walkthrough lives in [`SPECIFICATION.md` §3](SPECIFICATION.md#3-current-architecture-v4).

---

## Design principles

```text
The wrapper is the trusted edge, but only if the CLI is isolated from it.
Transport belongs to the adapter, not to the architecture.
Every seat has a point of view, or the council becomes an echo.
The Arbiter keeps the history and does not distribute it.
Whoever joins later joins without preconceptions.
The AI never touches the control channel.
The human never types commands into the dialogue.
No participant holds privileges; the operator is not a participant.
Every session modifies only itself, within its own profile.
A skip is an empty TURN_END.
Reconnection goes through REG.
Automate what is measurable; the rest belongs to the moderator.
No debate runs without a cap.
```

Two of these deserve a note, because they are the ones people usually argue with:

**"Automate what is measurable."** Consilium does not try to detect when a debate has converged, and does not score debate quality. Neither is reliably computable, and a model's self-assessment of whether it said anything new is not usable data. Termination is a human decision. What *is* automated is mechanical: a cost cap so an unattended debate cannot run forever, and per-participant repetition detection so a returning moderator can see at a glance who is spinning in place.

**"Every seat has a point of view."** Assigning explicit, distinct stances is not a personalisation feature. Without it, models trained to be agreeable will agree — elegantly, at length, and uselessly — regardless of which brands are at the table.

---

## What the human moderator does

1. **Triggers** the debate with a topic and an objective.
2. **Leaves**, if they want to. The debate continues; *away* mode is a supported state, not a degraded one.
3. **Returns** to a `STATUS` showing turns elapsed, cost consumed against the cap, and which participants are repeating themselves.
4. **Steers**: injects a new element, changes phase, or seats a fresh participant carrying the point of view the discussion is missing.
5. **Ends** it — or lets the cost cap end it, which the transcript records as a distinct outcome.

A debate may reach no acceptable conclusion. That is a valid result, and the final synthesis pass is specified to handle it: *where the discussion got to, what was agreed, what was not, and what remains open.*

---

## Security posture

The threat model is declared explicitly in [`SPECIFICATION.md` §14.1](SPECIFICATION.md#141-threat-model), including what is deliberately **out of scope** (the host operator; anyone able to write inside the wrapper image). Declaring it is what makes it possible to *remove* machinery, not only add it: with everything running locally in early phases, mTLS is not needed — while one thing is needed immediately.

That thing is the sharpest point in the design. The orchestrated CLIs are **agents with shell, filesystem and network access**. If such a CLI shares a user, a filesystem or a network namespace with the wrapper that holds the credentials, then calling the wrapper "trusted" means nothing: the AI never needs to inject a command into the dialogue, because a shorter path exists. So OS-level isolation of the CLI — separate uid, no credentials on a readable path, no tools, restrictive network policy — is a hard requirement rather than a recommendation, and the last line of defence is *"the model has no tool"*, never *"the model was told not to"*.

---

## Roadmap

| Phase | Content | Gate |
|---|---|---|
| **0 — Proof of value** | Single script, in-process. Adapter interface, role prompts, round-robin, JSONL transcript, cost cap. No sockets, no TLS, no Docker. | Are the debates interesting? Do they add anything over asking one model? **If no, the project ends here** |
| **1 — Product layer** | Role library, `role_visible`, cold entry as steering, phases, final synthesis, re-entry instruments, context sanitisation, pluggable speaker policy | Is it usable by a moderator on a long debate without friction? |
| **2 — Process separation** | Arbiter and wrappers as separate processes, dual channel, full protocol, profile capabilities, **CLI OS isolation**, admin plane, persistent transcript | Does it survive crashes, hangs and misbehaving participants? |
| **3 — Network** | Direct mTLS on both channels, certificate capability profiles, revocation, rate limiting, per-participant containers | Only if remote participants are genuinely needed |

The ordering is deliberate and inverts the project's own history: **validate the value before building the perimeter.** Phase 0 exists to make it cheap to discover that the idea does not work.

The experiment that justifies phase 0: run the same topic twice — once with three participants and no assigned roles, once with three roles. Read both.

---

## Repository contents

| Path | What it is |
|---|---|
| [`SPECIFICATION.md`](SPECIFICATION.md) | The v4 specification. Normative. Includes the full changelog, superseded decisions with rationale, the original v1 document (Appendix A), the version transitions (Appendix B), and the review outcome (Appendix C) |
| `README.md` | This file |

The specification keeps its own history on purpose. Sections 1.1–1.4 record what changed at each version and why; the appendices record what was tried and dropped. If you are reading the design and something looks like an odd choice, the reason it is that way is usually written down.

---

## Open questions

Tracked in [`SPECIFICATION.md` §16](SPECIFICATION.md#16-open-implementation-questions). The two that can affect whether the project is viable at all:

- **Subscription terms of service** for automated orchestration and for concurrent instances, per provider. This is a question to answer outside the code — it can invalidate the approach regardless of how good the architecture is.
- **CLI-side context compaction over long debates.** Each CLI compacts its own context autonomously, which is a benefit rather than a problem, but after enough compactions a participant remembers the opening of the debate only in summarised form. To be measured, not pre-solved.

---

## Naming

*Consilium* — a council convened to advise. Also known during development as **DebateLoop** and **Disputatio**. The name is a reminder of the product objective: a council produces counsel, and a system that produces only a log has not finished its job.

---

## Contributing

The design is at the stage where argument is more valuable than code. If you disagree with a decision, Appendix C is the place to look first — the finding may already have been raised, accepted, downgraded or rejected, with the reasoning recorded. Issues that engage with that reasoning are welcome.
