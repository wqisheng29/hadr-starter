"""The deterministic change-gate for the 08:30 brief (Slice 7).

The load-bearing decision of the unattended machinery: does the morning brief
have anything MATERIAL to republish? It answers WITHOUT calling a model and
WITHOUT touching the network, so the expensive LLM/brief step wakes only when
this cheap, reproducible check says something changed — the model never decides
whether to run (ADR-0001 / ADR-0002, PRD #3 hybrid scheduling).

It is a thin, read-only reuse of Slice 5's diff: read the current ledger
(read-only, so the single-writer guarantee holds — the gate is a reader, never a
writer), load the last published snapshot, and compute ``build_diff``. "Material
change" is exactly ``Diff.has_material_change`` (any change bucket non-empty; an
all-Ongoing diff is not material). One extra, deterministic signal: a missing
dashboard artifact is treated as change (it trivially "would differ"), so a
bootstrap run publishes. We deliberately do NOT compare rendered HTML bytes — the
brief stamps a per-run "as of" line (and, from Slice 6, model prose), so byte
comparison would always differ and would drag a model into the gate. State is
compared, not pixels.

Deterministic: the clock is injected (no ``datetime.now()``), so the same ledger
+ frozen clock + same prior snapshot yields the same decision every time.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .clock import Clock, to_sgt
from .diff import SnapshotEvent, build_diff
from .ledger import read_events
from .published import load_latest_snapshot


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict, as data (never an exception). ``counts`` carries the
    per-bucket diff tallies for a legible run log."""

    changed: bool
    reason: str
    counts: dict[str, int]

    @property
    def flag(self) -> str:
        """The ``changed=true|false`` line a workflow branches on (GITHUB_OUTPUT)."""
        return f"changed={'true' if self.changed else 'false'}"


def _read_current(db_path: str | Path) -> list[SnapshotEvent]:
    """The current canonical events, read from the ledger READ-ONLY (SQLite
    ``mode=ro`` — a stray write would raise; the gate never writes). A ledger that
    does not exist yet (bootstrap, before the first tick commits one) reads as no
    events rather than crashing."""
    path = Path(db_path)
    if not path.exists():
        return []
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [SnapshotEvent.from_row(row) for row in read_events(conn)]
    finally:
        conn.close()


def evaluate(
    db_path: str | Path,
    published_dir: str | Path,
    clock: Clock,
    dashboard_path: str | Path | None = None,
) -> GateDecision:
    """Decide whether the 08:30 brief has anything material to republish.

    Pure with respect to the outside world (read-only ledger + snapshot read,
    injected clock, no model, no network). ``changed`` is True iff the current
    ledger has a material change since the last published snapshot, OR the
    dashboard artifact is absent (a bootstrap publish). Otherwise False — the
    guarded publish step is skipped and nothing is republished."""
    current = _read_current(db_path)
    today = to_sgt(clock.now()).strftime("%Y-%m-%d")
    previous = load_latest_snapshot(published_dir, before=today)
    diff = build_diff(current, previous)
    counts = diff.counts()

    if dashboard_path is not None and not Path(dashboard_path).exists():
        return GateDecision(True, "dashboard artifact absent — bootstrap publish", counts)
    if diff.has_material_change:
        return GateDecision(True, "material change since last brief", counts)
    return GateDecision(False, "no material change since last brief", counts)
