"""Fuzzy ReliefWeb<->event link decisions — the recorded, overridable tie-break.

This is Seam 2's testable core (PRD Q "Cross-feed identity", scenario 12). The
deterministic core resolves the easy links (GLIDE crosswalk); the *fuzzy residual*
— a prose-only ReliefWeb item with no usable GLIDE — is tie-broken by the model
at 08:30. Per ADR-0004 and the single-writer discipline (ADR-0006, refined by
PRD #3), that judgement must not let the briefing write ``ledger.db``:

* the **08:30 brief** (read-only on the ledger) EMITS decisions to a DISJOINT
  JSON file (``state/link_decisions.json``), and
* the **fast tick** (sole writer of ``ledger.db``) APPLIES them
  (``apply_link_decisions``), writing the ``feed_identifiers`` row that links a
  ReliefWeb id to a canonical event.

A decision is **recorded** (persisted in the file, and once applied, in the
ledger) and **overridable**: a later decisions file mapping the same ReliefWeb id
to a *different* canonical event re-points the link on the next apply. Same file
applied twice is a true no-op.
"""

import json
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

# How a link was decided, for the audit trail. GLIDE links never need a decision
# (they resolve deterministically); a recorded decision is a model tie-break or a
# human override.
METHOD_MODEL = "model"
METHOD_OVERRIDE = "override"


@dataclass(frozen=True)
class LinkDecision:
    """One recorded ReliefWeb->canonical-event link. ``reliefweb_id`` is the feed
    id; ``canonical_id`` is the event it describes. ``method`` records who decided
    (model vs. a human override) and ``note`` is free-text provenance."""

    reliefweb_id: str
    canonical_id: str
    method: str = METHOD_MODEL
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "reliefweb_id": self.reliefweb_id,
            "canonical_id": self.canonical_id,
            "method": self.method,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LinkDecision":
        return cls(
            reliefweb_id=str(data["reliefweb_id"]),
            canonical_id=str(data["canonical_id"]),
            method=data.get("method") or METHOD_MODEL,
            note=data.get("note"),
        )


def load_decisions(path: str | Path) -> list[LinkDecision]:
    """Read the decisions file, or ``[]`` if it does not exist. A decisions file
    is an OPTIONAL input to a tick — absence is normal (no fuzzy links yet), not an
    error."""
    p = Path(path)
    if not p.is_file():
        return []
    doc = json.loads(p.read_text(encoding="utf-8"))
    return [LinkDecision.from_dict(d) for d in doc.get("decisions", [])]


def merge_decisions(
    existing: list[LinkDecision], new: list[LinkDecision]
) -> list[LinkDecision]:
    """Merge this run's ``new`` decisions into the ``existing`` file, preserving
    what a human recorded. A brief only emits the *current* fuzzy residual, so a
    plain overwrite would silently drop a human ``METHOD_OVERRIDE`` (advertised in
    the /sitrep skill) — or any decision not re-emitted this run — before the tick
    applied it. A new decision replaces an existing *model* decision for the same
    ReliefWeb id (the model refined its pick), but NEVER clobbers an override
    (human wins); entries untouched this run are kept."""
    by_id: dict[str, LinkDecision] = {d.reliefweb_id: d for d in existing}
    for d in new:
        current = by_id.get(d.reliefweb_id)
        if current is not None and current.method == METHOD_OVERRIDE:
            continue  # never overwrite a human override with a model pick
        by_id[d.reliefweb_id] = d
    return list(by_id.values())


def write_decisions(path: str | Path, decisions: list[LinkDecision]) -> Path:
    """Write the decisions file (sorted by reliefweb_id for a stable, reviewable
    diff). Deterministic: same decisions in any order -> byte-identical file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "decisions": [
            d.to_dict() for d in sorted(decisions, key=lambda d: d.reliefweb_id)
        ],
    }
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p
