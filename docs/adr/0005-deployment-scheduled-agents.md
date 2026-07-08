# ADR-0005: Deployment — Claude Code scheduled cloud agents

- Status: Accepted
- Date: 2026-07-08

## Context

"Runs unattended, day or night" rules out anything that sleeps (e.g. a laptop).
The hybrid split means the cheap deterministic poller and the occasional
expensive LLM step have very different cost profiles.

## Decision

Use the harness's own cloud scheduling (routines / cron agents). No server to
manage; most aligned with the course. Two scheduled entries: a lightweight,
frequent poll+triage tick (~every 30 min) that shells out to the deterministic
Python, and the 08:30 full briefing run.

## Consequences

- No host or OS-level cron to operate; secrets live in the agent environment.
- Each tick is an agent invocation, which is heavier/pricier than a plain cron
  script — so the poll interval is ~30 min rather than ~10, and the fast tick
  stays thin (shell out to Python, do minimal agent reasoning).
- State cannot rely on a long-lived local disk → see ADR-0006.

## Alternatives considered

- **Always-on host + cron**: true 24/7, cheapest per-poll, full control, but a
  box to run and secure. Deferred; natural migration target if it outgrows the
  routine.
- **Local + system cron**: zero infra, but breaks "day or night" whenever the
  machine sleeps. Rejected for the unattended requirement.
