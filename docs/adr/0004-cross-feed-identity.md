# ADR-0004: Cross-feed identity — deterministic core + LLM tie-break at briefing

- Status: Accepted
- Date: 2026-07-08

## Context

Deciding two records are the same physical event is the hardest technical
problem here. Exact keys only go part way: GLIDE numbers arrive days late and
are often blank early; GDACS carries `source`/`sourceid` that points back at the
USGS id (near-exact for quakes); ReliefWeb records are narrative prose needing
fuzzy matching. The chosen `canonical_id` keys every "what changed" diff.

## Decision

Two tiers.

- **Deterministic core (all in code):** union of USGS `ids`; GDACS
  `(eventtype, eventid)`; GDACS `source`/`sourceid` → USGS id crosswalk;
  GLIDE → ReliefWeb↔GDACS; and spatiotemporal blocking (same hazard, time
  window, distance, magnitude tolerance) with thresholds in config. The fast
  alert path — almost all USGS↔GDACS quakes — is fully deterministic.
- **LLM tie-break at 08:30 only:** the fuzzy residual (mostly ReliefWeb prose ↔
  hazard events) is resolved by the LLM at briefing time, with its linking
  decision written to the ledger and overridable.

## Consequences

- Urgent alerts never depend on or vary with the LLM.
- Briefing-time canonical_ids for fuzzy links are mildly non-deterministic;
  mitigated by recording and allowing override of each LLM link decision.
- Spatiotemporal thresholds need tuning; edge-case mis-merges/misses possible.

## Alternatives considered

- **Deterministic only**: fully reproducible but hand-tuning thresholds to match
  ReliefWeb prose is brittle. Rejected for the fuzzy residual.
- **Per-feed, no merge**: simplest, never mis-merges, but the same quake appears
  three times and loses the corroborated-across-sources view. Rejected.
