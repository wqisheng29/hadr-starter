# ADR-0002: Three-behaviour cadence — silent ingest + urgent push + daily brief

- Status: Accepted
- Date: 2026-07-08

## Context

The README pulls in two directions: events should be "noticed within minutes,
day or night" (fast) but the human-facing artifact is a "morning report at
08:30" (slow), and the system should "stay quiet when nothing has changed."

## Decision

Run three behaviours:

1. A **fast ingestion loop** (~every 30 min) that quietly keeps the ledger
   current and never interrupts a human. "Noticed within minutes" = recorded,
   not alerted.
2. An **urgent-alert escape hatch**: a severe, high-confidence event breaks the
   silence immediately via push (see ADR-0007), rather than waiting for 08:30.
3. The **08:30 briefing** as the routine anchor.

## Consequences

- Quiet by default; only genuinely severe events interrupt before 08:30.
- Requires a defined alert threshold (deferred to the PRD) and a delivery
  channel (ADR-0007).
- Two schedules to operate; state must persist between them (ADR-0006).

## Alternatives considered

- **Two clocks, silent ingestion** (no urgent path): simplest, but a severe
  overnight event waits until 08:30. Rejected as operationally weak.
- **One clock, daily only**: cheapest, but cannot notice within minutes and
  sees fast overnight events only in retrospect. Rejected.
