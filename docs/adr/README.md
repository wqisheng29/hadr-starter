# Architecture Decision Records

One file per load-bearing decision. Captured from the 2026-07-08 architecture
interview (build-plan-product, step A). See `../../CONTEXT.md` for the shared
language and the architecture at a glance.

- [ADR-0001](0001-hybrid-runtime.md) — Hybrid runtime: deterministic core + LLM judgement layer
- [ADR-0002](0002-three-behavior-cadence.md) — Three-behaviour cadence: silent ingest + urgent push + daily brief
- [ADR-0003](0003-ledger-current-state-plus-published.md) — Ledger model: current state + last-published snapshot
- [ADR-0004](0004-cross-feed-identity.md) — Cross-feed identity: deterministic core + LLM tie-break at briefing
- [ADR-0005](0005-deployment-scheduled-agents.md) — Deployment: Claude Code scheduled cloud agents
- [ADR-0006](0006-state-store-sqlite-in-repo.md) — State store: SQLite committed to the repo
- [ADR-0007](0007-urgent-alert-push.md) — Urgent-alert channel: harness push notification
- [ADR-0008](0008-stack-python.md) — Implementation stack: Python
