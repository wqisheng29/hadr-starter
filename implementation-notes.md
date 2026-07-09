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
- **Reasoning-model truncation (verified live, 9 Jul 2026).** `glm-5.2` spends
  its budget on hidden `reasoning_content` first — ~760 completion tokens for a
  one-word answer. Too-small `max_tokens` yields HTTP 200 with `content: ""` and
  `finish_reason: "length"`; the client maps that to `ChatResult(ok=False)`
  instead of treating it as an answer. Default budget is 2048
  (`llm.DEFAULT_MAX_TOKENS`). The gateway also honors
  `"thinking": {"type": "disabled"}` (verified: cuts the same call to ~106
  tokens) — not exposed in the client yet; add it if Slice 6 cost matters.

### Agent loop — chat loop + tools (the `/goal`-able mechanism)

- **The five-part shape, all injected at the same seam.** `scripts/agent.py` is
  the chat loop (read → append to `messages` → send → print). The system prompt
  is a plain text file prepended as `messages[0]` (`--system`, default
  `prompts/agent_system.md`) — the point being that "standing orders" are just a
  file, which is all a CLAUDE.md is. Two tools live in `hadr/tools.py`:
  `fetch_feed` (fetch + `parse_usgs` → compact JSON events) and `write_dashboard`
  (assessed events → `reports/sitrep.html`). `hadr/agent.py::run_agent` is the
  loop: while the model returns `tool_calls`, run them, append each result as a
  `role:"tool"` turn, and go again; stop on a plain-text reply or a `max_steps`
  guard. That guard-able loop is exactly what a `/goal` checker would wrap.
- **Tool-calling added to `llm.py` without breaking the one-shot seam.**
  `complete()` gained an optional `tools=` arg (adds `tools` + `tool_choice:auto`
  to the payload); `ChatResult` gained `tool_calls` and a rebuilt `message` (the
  assistant turn to append verbatim before the tool results, per the OpenAI
  protocol). The reasoning-truncation guard now fires only when there are no tool
  calls, since empty `content` is expected when the model is only calling tools.
- **Failures are data, here too.** `ToolRegistry.dispatch` never raises — unknown
  tool, invalid-JSON arguments, or a handler exception all return an
  `{"ok":false,"error":...}` string the model reads and reacts to, so neither a
  bad feed nor a misbehaving model crashes the loop.
- **Tools take injected dependencies** (`fetch_feed` a `{name: FeedSource}` map,
  `write_dashboard` an out-path + `Clock`), so the whole layer is tested against
  fixtures + a `FrozenClock` with no network. Verified live end-to-end against
  `glm-5.2` (9 Jul 2026): it called `fetch_feed`, assessed the fixture quakes,
  dropped the sub-floor/null-mag ones, called `write_dashboard`, and summarised —
  no invented events. glm-5.2 does support tool-calling through the Go gateway.
- **Agent dashboard is a separate artifact.** `write_dashboard` writes
  `reports/sitrep.html` (git-ignored) via its own `agent_sitrep.html.j2` template,
  deliberately *not* the committed deterministic `dashboard.html` — the agent's
  output is model-authored and experimental, not the pipeline's product.
- **Review follow-ups (post-build).** Two subagents reviewed the slice; fixes
  applied: (1) `--model` now goes through `llm.from_env(model=...)` so it keeps
  an `OPENCODE_BASE_URL` env override instead of silently resetting to the config
  default (both `agent.py` and `check_model.py`; the latter's "endpoint:" line was
  also lying — it now prints `model.base_url`). Removed the duplicated key-reading
  helpers. (2) The materiality floor is no longer hardcoded in the system prompt:
  `prompts/agent_system.md` carries a `{{MIN_MAGNITUDE}}` placeholder filled from
  `config.MIN_MAGNITUDE` at load time, honouring "thresholds in config, not prose".
  (3) Added `tests/test_briefer.py` so the deterministic dashboard's autoescape is
  guarded directly (a regression to `select_autoescape()` would otherwise pass).
  (4) `complete()` coerces null content to `""` so a reply is never `None`.
  `--as-of` parse errors now exit cleanly instead of a traceback.

### Agent-loop reliability — fixing the empty morning run (9 Jul 2026)

The first live GitHub Actions run failed: the agent called `fetch_feed` (248
live events), then emitted a "let me assess and write the dashboard" preamble
and **stopped without calling `write_dashboard`** — no file, and the workflow's
publish gate correctly failed the job. Root cause was two compounding things: a
reasoning model (`glm-5.2`) was handed all 248 events *and* the default 2048
token budget, so it exhausted the budget on reasoning + preamble and truncated
(`finish_reason=length`) before it could emit the tool call. Fixes:

- **`fetch_feed` filters to material events at the seam.** It now drops events
  below `config.MIN_MAGNITUDE` and returns them strongest-first, with a
  `total_before_floor` count. Fewer, ordered events mean less reasoning and a
  smaller tool call — and it is a correctness win (the model no longer wades
  through ~250 sub-M2.5 records). See the deviation note below.
- **Agent token budget raised to 8192** (`agent.DEFAULT_MAX_TOKENS`), threaded
  through `run_agent(max_tokens=...)` and a new `scripts/agent.py --max-tokens`
  flag. The llm default (2048) stays — it is right for the one-shot smoke test;
  agent turns need room to think *and* emit a tool call.
- **Truncation guard broadened** (`llm.py`): a `finish_reason=length` turn with
  no tool calls is now a failure even when its content is non-empty (previously
  only empty content tripped it), so a truncated preamble can never be mistaken
  for the final answer. The agent loop now stops loudly instead of silently
  producing no dashboard.
- **System prompt** updated to say `fetch_feed` returns pre-filtered material
  events and that calling `write_dashboard` is mandatory (invoke it, don't
  narrate it), including the zero-material-events case.
- **HTTP timeout raised 60s → 180s and made configurable.** With the token fix
  in place the *next* live run got further and then hit the hardcoded 60s httpx
  timeout: glm-5.2 spent >60s reasoning over the feed and generating the
  `write_dashboard` call (`ReadTimeout`, surfaced loudly by the broadened guard).
  The per-call timeout now defaults to `config.OPENCODE_TIMEOUT_S` (180s) and is
  overridable via `OPENCODE_TIMEOUT`, threaded through `OpenCodeChatModel(...,
  timeout=)` and `llm.from_env`. This is a batch agent, not a latency-sensitive
  request, so a generous default is right.

### Test hardening + robustness fixes (post-slice-1 audit)

An adversarial audit of the merged slice-1 core surfaced two real robustness
bugs and several untested guarantees. Fixed here (deterministic core only, no
behaviour change on the shipped fixtures — the committed `dashboard.html` is
byte-unchanged):

- **One malformed feature no longer sinks the whole feed.** `parse_usgs` wrapped
  the per-feature parse so a single `KeyError`/`IndexError` (a feature missing
  `time`, a 1-element `coordinates`, a missing `geometry`) returned
  `ParseResult(ok=False)` — discarding *every* good record and freezing the
  ledger. On a real ~250-record USGS feed one junk record would block a day of
  quakes. Now a bad feature is **skipped and counted** (`ParseResult.skipped`),
  the good records survive, and the pipeline notes `skipped N malformed
  feature(s)`. Only a broken *document* shape (invalid JSON, no `features` list)
  is still `ok=False`. This is "failures are data" applied at feature
  granularity.
- **Non-finite magnitudes are treated as absent.** `json.loads` accepts the bare
  `NaN`/`Infinity` tokens; a `NaN` magnitude broke idempotency (`nan != nan`
  bumped `last_updated` every re-run) and an `inf` would clear the materiality
  floor. `_maybe_float` now maps any non-finite value to `None` (so it is dropped
  like a null magnitude), closing both holes at the parse seam.
- **New regression tests** (51 → 66): parser feature-skip + document-shape
  failures + magnitude coercion edges (string/int/null/NaN) + short/absent
  coordinates; the equal-magnitude tie-break that keeps `dashboard.html`
  byte-stable (`ORDER BY magnitude DESC, canonical_id ASC`); and the previously
  untested `scripts/run.py` CLI (happy-path exit 0 + artifacts, bad/naive
  `--as-of` exit 2, the required source group, and a graceful+idempotent run over
  a feed carrying a malformed feature).
### Slice 2 — cross-feed identity (USGS + GDACS → one canonical event)

- **The crosswalk is three deterministic tiers, resolved in priority order** in
  `matcher.resolve_canonical_id_gdacs`: (a) GDACS `source`/`sourceid` → USGS `id`
  (NEIC-sourced quakes are near-exact, so a NEIC record attaches to the USGS
  canonical event it duplicates); (b) shared **GLIDE**; (c) GDACS's own native
  key `{eventtype}:{eventid}` (stable across episodes). First hit wins; a new
  `gdacs:EQ:{eventid}` is minted only when none match. This is exactly the seam
  slice 1 left in `feed_identifiers` — GDACS adds `(gdacs, EQ:eventid)`,
  `(glide, …)`, and the `(usgs, sourceid)` crosswalk rows; no schema reshape.
- **USGS owns the physical facts; GDACS owns severity.** `reconcile_gdacs`
  touches only the GDACS-derived columns (`gdacs_eventid`, `gdacs_episodeid`,
  `gdacs_alertlevel`, `gdacs_episodealertlevel`, `glide`, `country`) — never the
  USGS magnitude/place/title on a combined event. A GDACS-only event (no USGS
  twin) is inserted from GDACS data instead.
- **`alertlevel` (event-max) vs `episodealertlevel` (latest) are stored
  distinctly**, introduced now because GDACS brings the fields: `alertlevel` is
  monotonic (the highest severity ever seen, "was it ever severe"),
  `episodealertlevel` is always the latest authoritative value. Slice 3/4 depend
  on this distinction.
- **GDACS is not magnitude-floored** (unlike USGS's M4.5 gate). Per the PRD
  materiality rule, presence in GDACS (any colour) *is* the gate — and dropping a
  GDACS record would throw away corroboration of an already-stored USGS event.
- **Per-feed graceful degradation.** `run(gdacs_source=…)` reconciles USGS then
  GDACS onto the same ledger; either feed being unreachable/unparseable is a
  banner + warning, never a crash, and the other feed's data still renders.
  `RunResult` gained `feed_statuses` (all feeds touched) while `feed_status`
  stays the USGS/primary feed for slice-1 callers.

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
- **Morning sitrep runs on GitHub Actions, not the harness's cloud scheduler.**
  ADR-0005 chose the harness's own routines/cron agents for deployment.
  `.github/workflows/sitrep.yml` uses a GitHub Actions `schedule` instead,
  because the operator asked for it and the repo already shipped an Actions
  scaffold (`sitrep.yml.disabled`, now retired). Only the daily 08:30 briefing
  is wired here; the ~30-min poll+triage tick from ADR-0005 is not. Timezone:
  cron is UTC and SGT is a fixed UTC+8, so 08:30 SGT == 00:30 UTC; the job fires
  at 00:00 UTC (08:00 SGT) to absorb Actions' scheduling latency and be ready
  *by* 08:30. The natural migration back to the ADR-0005 scheduler (or an
  always-on host) is unaffected — the workflow just shells out to
  `scripts/agent.py`.
- **`fetch_feed` applies the materiality floor, not just the model's prose.**
  The agent slice originally left filtering to the model (system prompt), but a
  reasoning model handed ~250 events truncated before writing the dashboard (see
  "Agent-loop reliability" above). `fetch_feed` now drops sub-floor events at the
  seam using `config.MIN_MAGNITUDE`, returning material events strongest-first
  plus `total_before_floor`. This honours "thresholds in config, not prose"
  (CLAUDE.md) and does not change the deterministic pipeline, which already
  applied the floor independently.
- **Sitrep is published to GitHub Pages, never committed.** The agent's output
  stays git-ignored (`reports/`), honouring the "agent output is not the
  committed `dashboard.html`" decision above: the workflow writes it to
  `public/index.html` and deploys via `upload-pages-artifact`/`deploy-pages`, so
  nothing lands in git history. The publish is gated on a non-empty artifact
  containing the "as of … SGT" line, because `scripts/agent.py --once` exits 0
  even when the model/tool fails (it reports failures to stderr, not via the
  exit code) — the workflow check is the compensating gate.
- **Autoescape bug fixed (was a latent stored-XSS).** CLAUDE.md requires
  "autoescape on for any third-party feed text rendered into HTML," and both
  `briefer.py` and the new `tools.py` used `select_autoescape()`. But its default
  extension list matches `.html`/`.htm`/`.xml`, *not* this project's compound
  `.html.j2` template names — so autoescape was silently **off**, and untrusted
  USGS `place`/`title` went into `dashboard.html` unescaped. Both environments now
  set `autoescape=True` unconditionally (every template here emits HTML from
  untrusted feed/model text). Verified: `<script>` in a `place` is now escaped;
  the committed `dashboard.html` is byte-unchanged because the real fixture data
  has no HTML metacharacters.
- **Slice 2 assumes USGS is reconciled before GDACS** in a combined `run()`. USGS
  is the detection layer, so processing it first means a NEIC `sourceid` attaches
  to the existing `usgs:` canonical event rather than minting a `gdacs:` one. The
  reverse order (GDACS-first) would mint a `gdacs:` event that a later USGS record
  cannot currently fold into — a full merge of two pre-existing canonical events
  is out of slice-2 scope (documented in `resolve_canonical_id_gdacs`). When two
  canonical_ids match within a tier, `sorted(...)[0]` keeps the pick deterministic.
- **Slice 2 adds six nullable GDACS columns to `canonical_events`** via the
  `CREATE TABLE IF NOT EXISTS` schema, with no migration — consistent with
  `state/` being a git-ignored, ephemeral artifact until slice 7. A pre-existing
  DB from slice 1 would lack the columns; the schema is meant to be rebuilt.
- **GDACS dashboard tags use inline styles**, deliberately, so the always-emitted
  `<style>` block is untouched and the **USGS-only `dashboard.html` stays
  byte-identical** to slice 1 (verified by diff against `HEAD:dashboard.html`).
  The sources tag renders only when >1 real feed corroborates a quake; GLIDE is a
  cross-feed disaster number, not a feed, so it is excluded from the shown sources.

<!-- Anything built that departs from the PRD or CLAUDE.md is recorded here,
     with the reason. An undocumented deviation is a bug. -->
