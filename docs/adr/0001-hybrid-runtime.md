# ADR-0001: Hybrid runtime — deterministic core + LLM judgement layer

- Status: Accepted
- Date: 2026-07-08

## Context

The three feeds are mutable databases that revise, merge, and delete records
after publication. Reconciling that state (dedup, cross-feed identity, change
detection) must be reproducible; a non-deterministic LLM in that hot path would
make "what changed" undefined. But impact assessment and briefing prose benefit
from judgement the LLM does well.

## Decision

Split responsibilities. Deterministic code owns the hot path: fetch, parse,
cross-feed identity, the event ledger, change detection, noise filtering. The
LLM is called only for the judgement-and-prose layer: impact assessment and
writing the briefing.

## Consequences

- Reconciliation is testable and reproducible; same input → same ledger state.
- Urgent-alert triggering does not wait on, or vary with, the LLM.
- Two codebases to maintain (Python core + prompts/skills), with a clear seam
  between them (the ledger).

## Alternatives considered

- **Agent-as-runtime** (Claude Code does everything each tick): most aligned
  with the course, but makes reconciliation non-deterministic. Rejected.
- **Pure pipeline** (no LLM at runtime): maximally reproducible but the brief
  reads mechanical and can't do nuanced impact judgement. Rejected.
