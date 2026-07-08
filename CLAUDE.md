# CLAUDE.md

<!-- Fill in at least three conventions below before your first prompt.
     An empty conventions file is also a decision — just not one you made. -->

## Language & tooling

Python for the deterministic core (see docs/adr/0008-stack-python.md):
`httpx` (HTTP), `feedparser` (RSS), `sqlite3` (stdlib, ledger), hand-rolled
haversine / `shapely` (spatiotemporal matching), Jinja (render dashboard.html).
The LLM/agent layer is confined to impact assessment and briefing prose
(see docs/adr/0001-hybrid-runtime.md).

## Test command

## Conventions

## Deviations policy
