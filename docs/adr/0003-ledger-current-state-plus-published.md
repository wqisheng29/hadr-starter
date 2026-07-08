# ADR-0003: Ledger model — current state + last-published snapshot

- Status: Accepted
- Date: 2026-07-08

## Context

The feeds revise themselves constantly (USGS magnitude/ID changes, GDACS
episode downgrades, ReliefWeb archives fizzled alerts). For the briefing to
report corrections ("the M6.8 was revised to M6.1") rather than only the latest
snapshot, the ledger must retain enough history to diff against.

## Decision

Keep one row per canonical event holding the latest reconciled values, plus a
stored snapshot of what each briefing asserted. "What changed since yesterday" =
diff current state against the last published snapshot, yielding: new /
upgraded / downgraded / retracted.

## Consequences

- Corrections and retractions are computable without full version history.
- The published snapshot is a first-class artifact, retained per briefing.
- Not a full audit log: intra-day revisions between two briefings are not each
  retained (only latest state vs last published). Acceptable for the brief's
  daily cadence.

## Alternatives considered

- **Event-sourced / append-only**: richest (full replay/audit), maps naturally
  to the feeds' own versioning, but more to build and get right. Deferred; can
  layer on later.
- **Current state + dirty flag**: lightest, but can't reconstruct the old value
  it's correcting — weak/no true retractions. Rejected (kills the core feature).
