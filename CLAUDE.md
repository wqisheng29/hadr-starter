# CLAUDE.md

## Language & tooling

Python 3.11+ for the deterministic core (see docs/adr/0008-stack-python.md).
Slice-1 deps (pinned in `requirements.txt`): `httpx` (HTTP), `Jinja2` (render
`dashboard.html`), `pytest` (tests). `sqlite3` is stdlib. `feedparser` (RSS) and
`shapely`/haversine (spatiotemporal matching) arrive in later slices — not
installed yet. The LLM/agent layer is confined to impact assessment and briefing
prose (see docs/adr/0001-hybrid-runtime.md).

Setup: `python3.12 -m venv .venv && . .venv/bin/activate && pip install -r
requirements.txt && pip install -e .`

## Test command

`pytest` (from the repo root, inside the venv). Tests are fixture-driven and use
an injected `FrozenClock` — they never hit the network.

## Conventions

- **Deterministic core, injected edges.** No `datetime.now()` or network calls
  inside pipeline logic — a `Clock` and a `FeedSource` are injected. Same fixture
  + same frozen clock ⇒ same ledger and byte-identical `dashboard.html`.
- **Failures are data, not exceptions.** Fetch and parse return
  `FetchOutcome`/`ParseResult`; the pipeline degrades gracefully and always
  renders the dashboard.
- **Thresholds in config, not prose** (`hadr/config.py`). Anything that must give
  the same answer twice lives in code, per `scripts/README.md`.
- **Autoescape on** for any third-party feed text rendered into HTML.
- Tests assert external behaviour at the feed-fetch seam (ledger state + rendered
  dashboard), never generated prose or implementation detail.

## Deviations policy

Anything that departs from the PRD (issue #3) or an ADR is recorded in
`implementation-notes.md` with its reason. An undocumented deviation is a bug.
