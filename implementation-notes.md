# Implementation notes

Kept by the agent, reviewed by you. One entry per working block.

## Decisions

### Slice 1 — walking skeleton (USGS → ledger → dashboard)

- **Tooling:** venv + pinned `requirements.txt` (not uv), targeting Python 3.11+
  (system Python is EOL 3.9.6; installed 3.12 via Homebrew). Only `httpx`,
  `Jinja2`, `pytest` — `feedparser`/`shapely` deferred to the slices that need them.
- **`canonical_id` is an assigned surrogate** (`usgs:<preferred_id>`), never a raw
  feed id; identity resolves through a `feed_identifiers` side table. This is the
  seam slice 2 needs and is what keeps a quake as one event when its preferred `id`
  changes (union-of-ids).
- **Injected `Clock` + `FeedSource`** (dependency injection) rather than a fixture
  flag threaded through logic — keeps the pipeline pure and the fetch boundary the
  single test seam. No freegun/monkeypatch.
- **Idempotency via conditional update:** `last_updated` is bumped only when a
  tracked field actually changes, so a plain re-run is a true no-op even under a
  real clock (not just a frozen one).
- **`null` magnitude is treated as below the floor** (dropped), decided explicitly.

## Open questions

## Deviations

<!-- Anything built that departs from the PRD or CLAUDE.md is recorded here,
     with the reason. An undocumented deviation is a bug. -->

- **Four modules, not four scripts.** ADR-0008 names `poll.py`/`match.py`/
  `ledger.py`/`brief.py` as separate scripts; slice 1's DoD wants a *single*
  command, so the four responsibilities are modules inside a `hadr/` package
  orchestrated by `pipeline.run()` behind one CLI (`scripts/run.py`). The seams
  are preserved as module boundaries; a later slice can split processes if
  scheduling needs it.
- **`state/` is git-ignored for slice 1.** ADR-0006 commits `state/ledger.db`, but
  committing binary churn is premature until the single-writer / commit-on-change
  logic (slice 7) exists. `ledger.db` is treated as a produced artifact for now;
  `dashboard.html` is still committed (it is the product).
- **Reconcile is insert/update-only.** A quake absent from a later fetch is left
  as-is, never deleted — retraction/aged-out status is deliberately slice 3+.

### Agent harness (5 levels) — `hadr/agent.py`, `hadr/tools.py`, `scripts/chat.py`

- **Harness is a separate entrypoint (`scripts/chat.py`), not `scripts/run.py`.**
  The deterministic pipeline (`run.py`) and the LLM agent harness (`chat.py`) are
  distinct commands sharing the `hadr` package — the hybrid-runtime seam
  (ADR-0001) is the ledger/feed-source, not a single fused CLI.
- **`ChatModel` is injected, like `FeedSource`/`Clock`.** The agent loop is pure
  w.r.t. the network: `HttpChatModel` is the one edge; tests use fake/scripted
  models. Same discipline as the deterministic core.
- **`write_dashboard` tool renders its own template (`agent_dashboard.html.j2`).**
  The deterministic core's `dashboard.html.j2` (raw feed fields) is untouched;
  the agent's adds an `assessment` column (the LLM judgement layer, ADR-0001).
- **Autoescape is `True`, not `select_autoescape()`.** `select_autoescape()` only
  matches filenames ending in `.html`/`.xml`; templates here end in `.html.j2`,
  so it silently left autoescape off. Set `autoescape=True` explicitly to honour
  the "autoescape on for third-party feed text" convention (CLAUDE.md).

<!-- Anything built that departs from the PRD or CLAUDE.md is recorded here,
     with the reason. An undocumented deviation is a bug. -->
