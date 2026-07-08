# ADR-0008: Implementation stack — Python

- Status: Accepted
- Date: 2026-07-08

## Context

The deterministic core (ADR-0001) does feed fetching, RSS/JSON parsing, the
spatiotemporal matching (haversine distance, time/magnitude windows), SQLite
access, and rendering `dashboard.html`. Library ecosystems for geo and feed
parsing differ a lot by language.

## Decision

Write the deterministic core in Python: `sqlite3` (stdlib), `httpx` for HTTP,
`feedparser` for the RSS quirks, `shapely`/hand-rolled haversine for distance
matching, mature date handling for the feeds' three date formats, and Jinja to
render `dashboard.html`.

## Consequences

- Richest ecosystem for the ingestion + reconciliation + geo work.
- The scheduled agent shells out to Python scripts (`poll.py`, `match.py`,
  `ledger.py`, `brief.py`).
- Dashboard is templated (Jinja) from ledger data.

## Alternatives considered

- **TypeScript / Node**: one toolchain end-to-end and natural HTML/JSX
  templating, but weaker geo libs (DIY haversine) and RSS via `fast-xml-parser`.
  Rejected; the geo/feed-parsing ecosystem tipped it to Python.
