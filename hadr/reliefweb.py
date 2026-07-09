"""A minimal ReliefWeb record — curated humanitarian context (Slice 6).

ReliefWeb is the third, curated feed (USGS = detection, GDACS = triage,
ReliefWeb = confirmation/context). This slice deliberately does NOT build the RSS
or v2-API fetch (out of scope, issue #9 — it is a drop-in behind this normalised
record once an ``appname`` is approved). We represent only what the brief needs
to link an item to a canonical event and to cite it honestly.

Copyright posture (PRD user story 35): a ReliefWeb body is third-party copyright
with an AI-content clause, so the brief renders **excerpt + link + attribution
only** — never the full body. ``excerpt`` is a short, already-truncated snippet;
nothing here reproduces a whole report.

Linking is two-tier, mirroring the ``matcher`` seam:

* **Deterministic** — a ReliefWeb item carrying a GLIDE that already resolves to a
  canonical event joins WITHOUT the model (``resolve_glide``).
* **Fuzzy residual** — a prose-only item (no usable GLIDE) is left for the model
  to tie-break at 08:30, and that decision is RECORDED + overridable
  (``hadr/link_decisions.py`` + ``ledger.apply_link_decisions``).
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .fetch import GLIDE_SOURCE

# Attribution string the brief renders next to every excerpt. Kept here so the
# licensing posture lives in code, not scattered through templates.
ATTRIBUTION = "Source: ReliefWeb"


@dataclass(frozen=True)
class ReliefWebRecord:
    """One ReliefWeb disaster item, normalised to the few fields the brief uses.

    ``glide`` is the cross-feed disaster number (may be empty). ``excerpt`` is a
    short snippet only (copyright); ``url`` links back to the primary record.
    """

    id: str
    title: str
    url: str
    excerpt: str
    glide: str | None = None
    source: str = ATTRIBUTION

    @classmethod
    def from_dict(cls, data: dict) -> "ReliefWebRecord":
        return cls(
            id=str(data["id"]),
            title=data.get("title") or "",
            url=data.get("url") or "",
            excerpt=data.get("excerpt") or "",
            glide=(data.get("glide") or None),
            source=data.get("source") or ATTRIBUTION,
        )


def load_fixture(path: str | Path) -> list[ReliefWebRecord]:
    """Load a recorded ReliefWeb payload (a JSON list of items) off disk.

    The offline, deterministic path — the analogue of ``FixtureFeedSource`` for a
    feed whose live fetch this slice does not build. A missing file is an empty
    list (failures are data), never a crash."""
    p = Path(path)
    if not p.is_file():
        return []
    doc = json.loads(p.read_text(encoding="utf-8"))
    items = doc.get("items", doc) if isinstance(doc, dict) else doc
    return [ReliefWebRecord.from_dict(item) for item in items]


def resolve_glide(conn: sqlite3.Connection, record: ReliefWebRecord) -> str | None:
    """Deterministic GLIDE join: the canonical event this item's GLIDE already
    points at, or None. Reuses Slice 2's crosswalk seam (a GLIDE stored in
    ``feed_identifiers`` under the GLIDE namespace), so a GLIDE-carrying ReliefWeb
    item links WITHOUT the model. Returns the lexicographically-first match if a
    GLIDE (wrongly) fans out, keeping the pick deterministic."""
    if not record.glide:
        return None
    rows = conn.execute(
        "SELECT DISTINCT canonical_id FROM feed_identifiers "
        "WHERE source = ? AND feed_id = ?",
        (GLIDE_SOURCE, record.glide),
    ).fetchall()
    ids = sorted(row[0] for row in rows)
    return ids[0] if ids else None
