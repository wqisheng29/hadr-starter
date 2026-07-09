# /sitrep — run the 08:30 morning brief

Produce the HADR earthquake situation report: read the ledger, diff it against
the last published snapshot, characterise each material event's impact, and write
`dashboard.html`. This is the Day-2 skill artefact; it drives the deterministic
briefer (`scripts/brief.py`), optionally with the LLM judgement layer.

## When to use

At 08:30 SGT, or on demand to preview the brief. The **fast tick**
(`scripts/run.py`) must have built `state/ledger.db` first — the brief only
*reads* the ledger (single-writer discipline, ADR-0006).

## Steps

1. **Ensure the ledger is current.** If unsure, run one tick:
   `python scripts/run.py` (fetches USGS + GDACS → reconciles `state/ledger.db`).
   *Model: none — this is deterministic Python.*

2. **Write the brief.**
   - Deterministic (no key needed, always safe):
     `python scripts/brief.py --db state/ledger.db --out dashboard.html --published-dir state/published`
   - With judgement (impact prose + fuzzy ReliefWeb tie-break), needs
     `OPENCODE_API_KEY` in the env:
     `python scripts/brief.py ... --model --reliefweb fixtures/reliefweb/slice6.json --link-decisions-out state/link_decisions.json`
   *Model: the impact basis and the fuzzy ReliefWeb link use the injected
   `ChatModel` (config default `glm-5.2`). A model error degrades to the
   deterministic basis — the brief always renders.*

3. **Hand the decisions file to the next tick.** The brief NEVER writes the
   ledger; its fuzzy-link tie-breaks go to `state/link_decisions.json`. The next
   `scripts/run.py` applies them (recorded + overridable — edit that file to
   correct a mis-link, and the next tick re-links). *Model: none.*

## Honesty rules (baked into the output, do not override)

- Scope is **earthquakes only**; the brief renders a fixed "what this monitor
  cannot see" disclosure (floods/landslides/heat/conflict, ~M4.5–5.0
  completeness, `tsunami:1` ≠ a NOAA warning). It is a config constant, not prose.
- ReliefWeb text is third-party copyright: **excerpt + link + attribution only**,
  never the full body.
- Which events are in scope and their severity are **deterministic** (Slice 3/5);
  the model writes prose and tie-breaks fuzzy links, nothing more.
