# ADR-0006: State store — SQLite committed to the repo

- Status: Accepted
- Date: 2026-07-08

## Context

Cloud-scheduled agents (ADR-0005) share no long-lived local disk, so the
"current state + last-published" store (ADR-0003) must persist somewhere durable
each tick can read and write. This also determines the audit trail.

## Decision

Persist the ledger as a SQLite file committed to the repo (`state/ledger.db`).
Each run reads it, reconciles, writes it back, and commits. Runs are serialized
by the schedule, so there is no write contention.

To recover a human-readable trail (SQLite blobs diff opaquely in git), also
commit each 08:30 briefing as a readable file (`state/published/<date>.json`
and/or the rendered markdown), so the published-snapshot history is inspectable
even though the DB is not.

## Consequences

- Real SQL queries, transactions, and joins across events/published tables.
- Git carries the state, but `git log` on the `.db` is opaque — history is
  queried with SQL, not diffs; the committed JSON/markdown briefs restore a
  readable record of what was asserted.
- Binary churn in git history (one blob rewrite per commit). Acceptable at this
  scale.

## Alternatives considered

- **JSON committed to repo**: diff-friendly, `git log` = free readable audit
  trail, but weaker querying. Rejected in favour of SQL power (mitigated above).
- **External managed store** (Turso/Postgres/S3): durable and concurrent, but
  adds a dependency + secrets and moves the audit trail off the repo. Deferred.
