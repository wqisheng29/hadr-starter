# CONTEXT

Shared language and the architecture at a glance for the HADR monitor.
Produced from an architecture interview on 2026-07-08. The individual
decisions are recorded as ADRs under `docs/adr/`.

## What this is

An unattended monitoring agent over three disaster feeds (GDACS, USGS,
ReliefWeb). It keeps a reconciled picture of active disaster events, pushes an
urgent alert when a severe, high-confidence event appears, and publishes a
self-correcting situation report to `dashboard.html` at **08:30 Singapore
time**. It stays silent when nothing has changed.

The distinctive product idea: the feeds are **mutable databases**, but almost
every downstream tool reports once and never looks back. This agent's briefing
leads with *what changed since the last brief — including what we previously
told you that is no longer true* (upgrades, downgrades, retractions).

## The three feeds are three layers, not three copies

- **USGS — detection.** Earthquakes only, within minutes. Events are revised
  for ~72h (magnitude, location, preferred `id`, deletions).
- **GDACS — triage.** Multi-hazard, impact-scored (colour = likelihood of
  needing international assistance, not raw magnitude). Tens of minutes to days.
- **ReliefWeb — confirmation & context.** Human-curated humanitarian documents.
  Hours to days. A "disaster" exists here only once editors decide it matters.

## Key terms

- **Event** — a real-world hazard occurrence (one earthquake, one cyclone).
- **Episode (GDACS)** — one update to a GDACS event; a new episode is how GDACS
  represents "the situation changed." Keyed `(eventtype, eventid, episodeid)`.
- **Canonical event** — our merged record of one real-world event across feeds,
  addressed by a `canonical_id` we assign (see ADR-0004).
- **Ledger** — the persisted store of current canonical-event state
  (`state/ledger.db`, SQLite). See ADR-0003, ADR-0006.
- **Published snapshot** — what a given 08:30 briefing asserted, retained so the
  next brief can compute corrections. Written as a committed JSON/markdown file
  per day.
- **Provisional vs confirmed** — a fast, magnitude/location-based first read
  (provisional) vs. an impact-confirmed read once PAGER / GDACS scoring lands
  (confirmed). Impact signals are null for the first ~20–40 min.
- **Urgent alert** — a silence-breaking push for a severe, high-confidence
  event, sent before 08:30 (see ADR-0002, ADR-0007).
- **Briefing** — the 08:30 human-facing artifact (`dashboard.html`).

## Architecture at a glance

```
Claude Code scheduled cloud agents (ADR-0005)
  ├─ fast tick (~every 30 min): shell out to deterministic Python (ADR-0001, 0008)
  │     fetch → parse → cross-feed identity (deterministic core) → reconcile
  │     → write ledger.db (SQLite in repo, ADR-0003, 0006)
  │     └─ if severe + high-confidence → assess → PUSH now (ADR-0002, 0007)
  │
  └─ 08:30 tick: read ledger → diff vs last published snapshot
        → LLM assesses impact + resolves fuzzy ReliefWeb links (ADR-0004)
        → LLM writes self-correcting briefing → dashboard.html
        → commit ledger.db + published/<date>.json|md
```

Deterministic code owns the reconciliation hot path (reproducible, testable).
The LLM is confined to judgement and prose: impact assessment, fuzzy
cross-feed linking at briefing time, and writing the brief.

## Constraints that bound the design

- **Mutability**: treat every event as revisable for ~72h; a record leaving a
  feed's window does not mean the event ended (GDACS window ~4 days).
- **Impact ≠ magnitude**: trigger on impact signals (GDACS colour, PAGER), with
  a provisional→confirmed two stage because those signals arrive late.
- **Coverage blind spots**: floods are late/patchy (human-report driven, source
  defunct), no landslides/heat/conflict, USGS global completeness only ~M4.5–5.0,
  `tsunami:1` is NOT a tsunami warning (that's NOAA). The briefing should state
  what it cannot see.
- **Access**: ReliefWeb needs a pre-approved `appname` (human review — request
  early); build against its RSS meanwhile. Budgets: ReliefWeb 1000 calls/day;
  USGS 60s cache + gzip + `If-Modified-Since`; GDACS delta-poll on `datemodified`.
- **Licensing**: USGS public domain; GDACS attribution ("GDACS", CAP is CC BY 4.0);
  **ReliefWeb report bodies are third-party copyright** — link + short excerpt +
  attribution only, no wholesale republishing; there is an AI-content clause.

## Open questions for the PRD (step B)

Product/policy, deliberately deferred from the architecture interview:

1. **Hazard scope for the build** — earthquakes end-to-end first (USGS+GDACS+
   ReliefWeb), or all GDACS hazard types shallower?
2. **Urgent-alert threshold** — precise definition of "severe + high-confidence"
   (e.g. GDACS Red, or PAGER orange+, and what "confident" means before review).
3. **Noise filter** — what enters the ledger vs. is dropped (Green M4.5 quakes
   dominate); the provisional→confirmed promotion rules.
4. **Impact assessment & enrichment** — which products the LLM pulls (PAGER,
   ShakeMap, population exposure) and how the brief characterises severity.
5. **Retraction wording** — how corrections/downgrades are phrased for trust.
