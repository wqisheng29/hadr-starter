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

### Slice 3 — provisional → confirmed lifecycle

- **Confirmation is a fold over feed-native signals — NO enrichment fetch.**
  Every event is `provisional` on first detection and firms to `confirmed` when
  any one confirming signal is present in a feed record the pipeline already has:
  USGS `properties.status` == `reviewed` (automatic→reviewed is a settle), a
  non-null USGS PAGER colour (`properties.alert`), or GDACS `istemporary` ==
  `"false"` (a settled, no-longer-temporary alert; a settled ShakeMap flips it
  off). We deliberately do **not** fetch PAGER/ShakeMap — that's a later slice.
- **The rule's inputs live in `config`, not prose.** `CONFIRMED_USGS_STATUS =
  "reviewed"`, the `STATUS_PROVISIONAL`/`STATUS_CONFIRMED` labels, and the
  headline/material set `MATERIAL_ALERT_LEVELS = {"orange","red"}`. The ledger's
  status refresh and the briefer's classification both read these — no magic
  strings in logic or the template.
- **One deterministic refresh helper, called from both reconcile paths.**
  `ledger._refresh_status` recomputes status from the stored signals
  (`usgs_status`, `pager_alert`, `gdacs_is_temporary`) after each upsert, so
  either feed's settle promotes the event. It is **sticky**: `_lifecycle_status`
  never regresses a `confirmed` row to `provisional` (a signal that un-sets
  doesn't un-settle). A status change bumps `last_updated`; no change writes
  nothing.
- **The refresh is not counted in `rows_written`.** Promotion is a derived
  consequence of a feed update that the tracked-field comparison already counted
  (e.g. `usgs_status` and `gdacs_is_temporary` are now in `_TRACKED`/
  `_GDACS_TRACKED`), so counting the refresh too would double-count one logical
  change and break the existing `rows_written` assertions. The status write still
  bumps `last_updated` on a real change; on a re-run the status is already correct
  so it writes nothing (combined USGS+GDACS re-run stays `rows_written == 0`,
  byte-identical dashboard).
- **`istemporary` models the GDACS "settled" signal.** Parsed `"true"/"false"` →
  bool with missing/unrecognised → `True` (conservative "not yet settled"), and
  stored as INTEGER `0/1` in `gdacs_is_temporary` so the idempotency comparison
  round-trips cleanly through SQLite (mirrors the care Slice 2 took). The stored
  column follows the *latest* episode (like `episodealertlevel`); confirmation
  itself is what's sticky, so a later temporary episode can't demote a confirmed
  event.
- **Dashboard headlines material/confirmed, folds routine.** The briefer splits
  events into a headline table (with a `provisional`/`confirmed` tag per row) and
  a collapsed "Routine / ongoing (N)" list below the fold. Material = confirmed
  OR current severity in `MATERIAL_ALERT_LEVELS` OR magnitude ≥
  `HEADLINE_MIN_MAGNITUDE` (see below). The empty-state ("No earthquakes at or
  above the materiality floor.") and the "as of" SGT header are unchanged.
- **A strong provisional quake headlines on magnitude alone**
  (`HEADLINE_MIN_MAGNITUDE`, default M6.0), added after review. "Routine" means a
  *minor* quake near the M4.5 floor, not a major one that simply hasn't been
  reviewed yet — folding a fresh M6.8 below the fold because its PAGER/ShakeMap
  hasn't landed (they take ~20–40 min) would bury the single most important event.
  This governs dashboard *surfacing* only and does **not** loosen the urgent-push
  rule, which stays confirmation-only with no magnitude escape hatch (Slice 4).

### Slice 4 — urgent-alert decision + push

- **The decision is a pure function** (`alert.decide_alert`), importing only
  `config`. Whether a severe, high-confidence quake breaks silence — *and the
  words of the push* — are deterministic core, never the LLM (PRD Q2, ADR-0002/7):
  same facts + same "as of" ⇒ same fire/no-fire + byte-identical message. The
  message (`compose_message`) is a fixed template of ledger facts, magnitude
  formatted `M%.1f` to match the dashboard.
- **Severe = impact, never magnitude.** GDACS latest-episode `episodealertlevel`
  Red (`URGENT_GDACS_LEVELS`) or USGS PAGER Orange/Red (`URGENT_PAGER_LEVELS`).
  **High-confidence = the Slice-3 lifecycle `status` is confirmed**, so a
  pre-ShakeMap GDACS Red (still provisional) does not fire; a strong-but-
  unconfirmed quake never fires (no magnitude escape hatch).
- **Escalation-only, one-push-per-event.** The event's current urgent level is the
  max-rank qualifying signal (`URGENT_LEVEL_RANK`); it fires only when strictly
  higher-rank than the persisted `last_pushed_level`. First severe+confirmed
  fires; a re-run at the same level does not; Orange→Red fires again; a downgrade
  never fires. This survives stateless ticks because `last_pushed_level` is a
  ledger column.
- **Delivery is an injected sink** (`push.PushSink`), like `FeedSource`/`Clock`;
  tests inject `RecordingPushSink` and assert the decision as data. `run()` gains
  `push_sink=None`; the push is evaluated only when a sink is supplied (the fast
  tick), never in the `push_sink=None` brief context — matching the PRD hybrid
  split. Fired alerts are returned in `RunResult.alerts_pushed`. No real-network
  push here (the CLI wires no production sink yet).

### Slice 5 — self-correcting 08:30 brief (deterministic diff)

- **The diff is pure and LLM-free** (`hadr/diff.py`): `impact_tier` +
  `classify` + `build_diff`, unit-tested in isolation. `impact_tier(status,
  gdacs_episodealertlevel, pager_alert)` is the MAX severity rank across the GDACS
  episode colour and the PAGER colour on one shared 0/1/2 scale
  (`GDACS_ALERT_RANK` + a new `PAGER_ALERT_RANK` where yellow/green share the low
  tier, per PRD Q5). Confirmation is deliberately NOT folded into the tier —
  provisional→confirmed-severe is a separate branch in `classify` — so a
  re-review can never masquerade as a colour move.
- **The three distinctions the PRD forbids conflating, encoded by reading
  `status` BEFORE tier:** a colour drop (even Red→Green) is **Downgraded** (still
  real); only `status == retracted` (a positive withdrawal) is **Retracted**; a
  magnitude revision with the tier unchanged is a **Correction**, not a re-rank;
  `status == aged_out` is **Aged-out** ("not confirmed ended"). Only the
  *transition into* a terminal status is a change bucket — an already-terminal
  event that was terminal in the last brief folds to **Ongoing**, so a re-brief is
  idempotent.
- **Retraction/aged-out now live in the ledger** (`ledger.reconcile_absences`, run
  after both feeds reconcile in `run()`). Two new statuses
  (`STATUS_RETRACTED`/`STATUS_AGED_OUT`). A USGS record with
  `status == "deleted"` (`DELETED_USGS_STATUS`) is marked retracted inline in
  `reconcile` without folding in its (stale) magnitude. Disappearance detection is
  the load-bearing heuristic — see the deviation note below.
- **Brief is READ-ONLY on the ledger, enforced by SQLite** (`brief._connect_readonly`
  opens `file:…?mode=ro`, so a stray write raises). It reads current events + the
  last published snapshot, computes the diff, renders `dashboard.html` with a
  "Since the last brief" section on top, THEN writes today's snapshot LAST — the
  two writes (`published/<SGT-date>.json`, `dashboard.html`) are disjoint from
  `ledger.db`. The brief never pushes and runs no model (where prose goes is a
  deterministic placeholder for Slice 6).
- **Published snapshots** (`hadr/published.py`) are readable JSON (indent=2,
  sort_keys, events sorted by canonical_id) with schema version + SGT/UTC "as of".
  `load_latest_snapshot` reads the lexicographically greatest filename STRICTLY
  LESS than today's SGT date — "the last brief" is always a prior one, which keeps
  a same-day re-render byte-identical and makes the diff never read its own
  just-written snapshot.

### Slice 6 — LLM judgement layer (impact prose, cannot-see disclosure, fuzzy ReliefWeb links)

- **The model is an injected edge in the brief, judgement-only.**
  `brief.write_brief(..., model: ChatModel | None = None, reliefweb=..., link_decisions_out=...)`.
  When a model is given it (a) writes a one-line **impact basis** for each MATERIAL
  event (reusing `briefer._is_material`) and (b) tie-breaks fuzzy ReliefWeb links.
  When `model is None`, or a call returns `ChatResult(ok=False)`, or the reply is
  empty, it degrades to a **deterministic basis** (`_deterministic_basis`: GDACS
  colour + PAGER colour + magnitude + status from ledger facts). The model NEVER
  decides scope or severity — that stays deterministic (Slice 3/5). Same fixtures +
  frozen clock + a scripted model ⇒ deterministic WIRING; prose is never asserted.
- **"What this monitor cannot see" is a config constant**
  (`config.CANNOT_SEE_DISCLOSURE`), always rendered into the brief (PRD user story
  4). Deterministic text, not model output — it is a policy statement that must read
  the same every morning.
- **Seam 2, the testable core: recorded + overridable link decisions.** The fuzzy
  ReliefWeb↔event tie-break is emitted to a DISJOINT JSON file
  (`state/link_decisions.json`, `hadr/link_decisions.py`); the fast tick applies it
  via `ledger.apply_link_decisions` (INSERT the `feed_identifiers` reliefweb row, or
  UPDATE it to a new canonical_id on override). RECORDED (persisted) + OVERRIDABLE (a
  later file mapping the same reliefweb id elsewhere re-links next tick) + idempotent
  (re-apply writes nothing). A decision for a non-existent canonical event is skipped,
  not an FK crash (failures are data). Wired into `pipeline.run(link_decisions_path=…)`
  — default `None`, so existing callers/tests are unaffected.
- **ReliefWeb is represented minimally, NOT fetched** (`hadr/reliefweb.py`:
  `ReliefWebRecord` = id/title/url/excerpt/glide?/source; `load_fixture`;
  `resolve_glide`). Per issue #9 the RSS/API fetch is out of scope — a fixture
  stands in (`fixtures/reliefweb/slice6.json`). Deterministic GLIDE links resolve
  WITHOUT the model (reusing Slice 2's crosswalk seam via `feed_identifiers`); only
  the fuzzy residual (no usable GLIDE) uses a recorded model decision. Copyright:
  the brief renders **excerpt + link + "Source: ReliefWeb"** only (there is no
  full-body field), respecting third-party copyright + the AI-content clause.
- **The model reply for a link tie-break is VALIDATED against the real candidate
  ids** — a hallucinated or `NONE` answer resolves to no link, so the model can only
  pick an event that exists, never mint one.
- **`/sitrep` skill** (`skills/sitrep/SKILL.md`, the Day-2 ≥1-skill artefact)
  documents running the brief via `scripts/brief.py`, deterministic by default and
  with `--model`/`--reliefweb`/`--link-decisions-out` for the judgement layer, and
  restates the honesty rules.

## Open questions

## Deviations

<!-- Anything built that departs from the PRD or CLAUDE.md is recorded here,
     with the reason. An undocumented deviation is a bug. -->

- **Slice 6: the impact basis cites only the fields the ledger actually carries**
  (GDACS episode colour, PAGER colour, magnitude, status), NOT the richer PAGER
  loss bins / ShakeMap MMI+exposure the PRD (Q4) envisions. Those come from
  enrichment fetches (PAGER/ShakeMap/GDACS `url.details`) that this slice does not
  build — the ledger has no columns for them. The model prompt asks it to cite the
  products it was *given*, and the deterministic fallback cites the colours; the
  seam is in place to feed richer products later without shape change. Recorded so
  the gap between the prose's ambition and the available facts is explicit.
- **Slice 6: ReliefWeb is fixture-backed, not fetched** (issue #9 out-of-scope: the
  RSS feed / v2 API). `hadr/reliefweb.py` + `fixtures/reliefweb/slice6.json` stand
  in behind the same normalised `ReliefWebRecord` the live fetch will produce, so
  linking + rendering + copyright handling are all exercised offline. No
  `feedparser` dependency added.
- **Slice 6: `state/link_decisions.json` is a git-ignored produced artifact**
  (under the already-ignored `state/`), like `state/ledger.db` and
  `state/published/`. Committing it and the single-writer commit-on-change
  coordination is Slice 7. Tests use a tmp path; the brief CLI emits the file only
  when `--model` is on (a keyless deterministic brief writes no decisions file and
  touches no extra paths).
- **Slice 6: `apply_link_decisions` makes a linked ReliefWeb id show up in an
  event's `sources`** (via `_sources_for`, which already excludes only GLIDE). This
  is intended — ReliefWeb corroborates — and never triggers a false absence-mark,
  because `reconcile_absences` requires ALL of an event's sources reachable and
  ReliefWeb is never fetched (never in `reachable`), so such events are simply left
  alone (conservative). No existing test links ReliefWeb, so none is affected.
- **Slice 6: `brief.py` imports `briefer._is_material`** (a leading-underscore
  helper) to classify which events get an impact basis, rather than duplicating the
  materiality rule. Reuse over a copy keeps the single config-driven definition
  authoritative; the alternative was to promote it to a public name, deferred to
  avoid touching Slice 3's surface.

- **Slice 5: the disappearance-detection heuristic** (`ledger.reconcile_absences`)
  distinguishes retraction from aged-out from an outage using config windows
  (`USGS_WINDOW_HOURS = 72`, `GDACS_WINDOW_DAYS = 4`). After both feeds reconcile,
  an event NOT seen this run is marked **aged_out** if its age exceeds the MAX
  window across its vouching feeds, else **retracted** (within-window withdrawal).
  Three guards keep it conservative: (a) an event whose vouching feeds were not
  ALL reachable this run is left alone — an outage must never read as a
  withdrawal (degradation); (b) already-terminal rows are never re-classified
  (sticky); (c) an event with no origin time is skipped. The "seen" sets are
  threaded up from `_run_usgs`/`_run_gdacs` (resolved via the existing matcher,
  deterministic post-reconcile) and `run()`'s signature is unchanged, so existing
  callers/tests are unaffected — same-fixture re-runs see every event, so nothing
  is spuriously marked. Absence marks bump `last_updated` (a real state change,
  unlike a push) and DO count toward `rows_written`; the count is 0 on every
  pre-Slice-5 fixture, so existing `rows_written` assertions hold.
- **Slice 5: `state/published/` is a git-ignored produced artifact** (like
  `state/ledger.db`). ADR-0006 commits the readable snapshot as the audit trail;
  committing it — and the single-writer commit-on-change coordination — is Slice 7.
  For now the brief writes it under the git-ignored `state/` and tests use a tmp
  `published_dir` so run 2 reads run 1's snapshot.
- **Slice 5: the brief renders its OWN template** (`templates/brief.html.j2`,
  its own autoescape=True Jinja env in `brief.py`), structurally different from
  the tick's `dashboard.html.j2` — the "Since the last brief" section has no
  analogue in the current-state tick dashboard. The tick's `run()` output and the
  committed `dashboard.html` are byte-unchanged (verified by diff). Both the brief
  and the tick may write `dashboard.html`; wiring the 08:30 brief as the canonical
  writer of the committed file is Slice 7's scheduling concern.
- **Slice 5: the brief CLI (`scripts/brief.py`) does not fetch or reconcile** — it
  only reads the ledger (read-only) + last snapshot and writes the disjoint
  artifacts. `scripts/run.py` remains the (USGS-only) tick CLI; it has no GDACS
  flag (a pre-existing slice-1 limitation), so a full GDACS-inclusive brief is
  driven through `pipeline.run(gdacs_source=…)` (as the tests do), not the tick CLI.
- **Slice 4: `last_pushed_level` is not a reconcile-tracked field.** It records the
  alert level last pushed (one-push-per-event across stateless ticks) but is
  excluded from `_TRACKED`/`_GDACS_TRACKED`, so it never affects reconcile
  idempotency or `rows_written`. `record_pushed` also deliberately does NOT bump
  `last_updated` — a push is a delivery side effect, not a change to feed-derived
  facts, and bumping it would pollute Slice 5's "changed since last brief" diff.
- **Slice 4: GDACS Orange is not an urgent signal** (PRD: GDACS *Red* only), so
  the clean Orange→Red escalation runs on the PAGER axis (PAGER Orange→Red). A
  GDACS Orange→Red sequence yields exactly one Red push (Orange never pushed).
- **Slice 4: no production push sink is wired.** The decision + message are
  complete and tested via `RecordingPushSink`; delivery over the harness's native
  push is left to Slice 7's scheduled agent (real network is out of this slice's
  scope). `scripts/run.py` runs with `push_sink=None`.
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
  *(Superseded in Slice 5: `reconcile_absences` now marks disappearance as
  `retracted`/`aged_out` — the row is still never DELETEd, only re-statused.)*
- **The committed `dashboard.html` changed structure this slice** (the
  byte-identical-to-slice-1 guarantee was slices 1–2 only). It is regenerated
  deterministically via `python scripts/run.py --fixture fixtures/usgs/all_day.json
  --as-of 2026-07-08T00:30:00Z --db <tmp> --out dashboard.html` and is
  byte-identical across fresh-DB re-runs. In the USGS-only `all_day.json` the M6.8
  Padang quake is `automatic` with a null PAGER, so it is **provisional** — but it
  still **headlines** (tagged provisional) because it clears
  `HEADLINE_MIN_MAGNITUDE`, while the M5.1 Hualien quake (`status=reviewed`) is
  confirmed and the M4.5 Avalon quake folds into "Routine / ongoing". The
  provisional/confirmed tag keeps an unreviewed read from being mistaken for a
  settled one.
- **Confirmation reads only in-feed signals (no enrichment).** Per the issue-#6
  scope, `confirmed` reflects PAGER/ShakeMap/review fields already present in the
  USGS/GDACS records; the pipeline never fetches PAGER or ShakeMap itself. That
  enrichment fetch is a later slice.
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
- **Slice 2 reconciles USGS before GDACS** in a combined `run()`. USGS is the
  detection layer, so processing it first means a NEIC `sourceid` attaches to the
  existing `usgs:` canonical event rather than minting a `gdacs:` one. (If GDACS
  arrived first and minted `gdacs:EQ:…`, a later USGS record still folds in
  correctly: `_link_gdacs_ids` writes the `(usgs, sourceid)` crosswalk row, so the
  USGS record resolves onto that same canonical event via `resolve_canonical_id` —
  no duplicate.) A genuine duplicate only arises for a non-NEIC, non-GLIDE pair
  sharing only physical location, which needs slice-3 spatiotemporal matching and
  is out of scope. A full merge of two *pre-existing* canonical events is likewise
  out of scope; when two canonical_ids match within a tier, `sorted(...)[0]` keeps
  the pick deterministic.
- **Multiple GDACS records collapsing to one canonical event fold before writing.**
  `reconcile_gdacs` groups records that resolve to the same `canonical_id`
  (aftershocks sharing a GLIDE, or several episodes of one eventid in one payload)
  and writes each group once from a deterministic fold — event-max
  `gdacs_alertlevel` across the group, latest-episode fields chosen by
  `(origin_time, episodeid, eventid)` (payload-order-independent). Writing
  per-record instead let records overwrite each other's columns and re-fire the
  UPDATE on every re-run (`last_updated` churn under a real clock, which would also
  poison slice 5's "since last brief" diff). A newly-minted GDACS-only event gets a
  base row inserted during the resolve pass so identifier links satisfy the
  `feed_identifiers → canonical_events` FK and a later record can find it by GLIDE.
  Found by an adversarial review subagent; the shipped tests missed it because they
  used a single record or separate `reconcile_gdacs` calls.
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
