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

### Model provider — OpenCode Go

- **Provider is OpenCode Go**, an OpenAI-compatible gateway
  (`https://opencode.ai/zen/go/v1`, bearer auth). Chosen because that is the key
  the operator has; it drops into a standard `/chat/completions` client.
- **`hadr/llm.py` is an injected edge**, the same shape as `FeedSource`/`Clock`:
  a `ChatModel` Protocol + `OpenCodeChatModel`, failures returned as
  `ChatResult(ok=False, ...)` rather than raised. Tests use `httpx.MockTransport`
  (no network), like the feed tests.
- **Key from `OPENCODE_API_KEY` env only** — never a flag or a stored value.
  Base URL / model overridable via `OPENCODE_BASE_URL` / `OPENCODE_MODEL`;
  defaults live in `config.py` (`glm-5.2`).
- **Go serves open-source coding models** (GLM/Kimi/DeepSeek/Qwen), *not* the
  frontier Claude/GPT/Gemini models (those are the separate pay-as-you-go Zen
  tier). If a slice later needs a frontier model, point the base URL at Zen.
- **No caller yet.** This is only the provider seam + a `scripts/check_model.py`
  smoke test to verify the key. Wiring it into impact assessment / briefing prose
  is Slice 6 (ADR-0001) and is deliberately not done here.

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

<!-- Anything built that departs from the PRD or CLAUDE.md is recorded here,
     with the reason. An undocumented deviation is a bug. -->
