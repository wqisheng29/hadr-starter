"""The 08:30 brief — READ-ONLY on the ledger, writes disjoint paths (Slice 5).

Single-writer discipline (ADR-0006, refined by PRD #3): the fast tick is the sole
writer of ``ledger.db``; the brief only *reads* it and the last published
snapshot, and writes two DISJOINT artifacts — today's ``published/<date>.json``
and ``dashboard.html``. To make "read-only" a guarantee rather than a promise,
the ledger is opened with SQLite's ``mode=ro`` URI, so any stray write raises.

The brief NEVER pushes (the urgent path is the fast tick's, per the PRD hybrid
split) and runs NO model this slice — where impact prose will go (Slice 6) the
diff structure and deterministic wording stand in. Same ledger + frozen clock +
same prior snapshot ⇒ byte-identical dashboard and snapshot.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from .clock import Clock, format_sgt, to_sgt
from .diff import (
    CHANGE_BUCKETS,
    ONGOING,
    Diff,
    SnapshotEvent,
    build_diff,
)
from .ledger import read_events
from .published import load_latest_snapshot, write_snapshot

# A dedicated env, autoescape unconditionally on (compound ``.html.j2`` names do
# not match select_autoescape()'s default list — same care as briefer.py).
_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class BriefResult:
    """Summary of a brief run, for the CLI + tests to assert as data."""

    out_path: str
    snapshot_path: str
    counts: dict[str, int]
    no_material_change: bool


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the ledger READ-ONLY. A write attempt raises — the single-writer
    guarantee is enforced by SQLite, not merely by convention."""
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_mag(magnitude: float | None) -> str:
    return f"M{magnitude:.1f}" if magnitude is not None else "M?"


def _event_view(ce) -> dict:
    e: SnapshotEvent = ce.event
    return {
        "mag": _fmt_mag(e.magnitude),
        "title": e.title,
        "place": e.place,
        "detail": ce.detail,
    }


def render_brief(diff: Diff, as_of_utc: datetime) -> str:
    """Pure render of a diff to the brief dashboard HTML."""
    change_buckets = [
        {"label": b, "events": [_event_view(ce) for ce in diff.get(b)]}
        for b in CHANGE_BUCKETS
    ]
    ongoing = [_event_view(ce) for ce in diff.get(ONGOING)]
    template = _env.get_template("brief.html.j2")
    return template.render(
        as_of=format_sgt(as_of_utc),
        has_material_change=diff.has_material_change,
        change_buckets=change_buckets,
        ongoing=ongoing,
    )


def write_brief(
    db_path: str | Path,
    out_path: str | Path,
    published_dir: str | Path,
    clock: Clock,
) -> BriefResult:
    """Read the ledger + last snapshot, compute the diff, render the dashboard,
    then publish today's snapshot. Order matters: the snapshot is written LAST and
    to a date-stamped path, so it never becomes its own diff baseline within a run
    and the diff always compares against a genuinely prior brief."""
    conn = _connect_readonly(db_path)
    try:
        current = [SnapshotEvent.from_row(row) for row in read_events(conn)]
    finally:
        conn.close()

    today = to_sgt(clock.now()).strftime("%Y-%m-%d")
    previous = load_latest_snapshot(published_dir, before=today)
    diff = build_diff(current, previous)

    Path(out_path).write_text(render_brief(diff, clock.now()), encoding="utf-8")
    snapshot_path = write_snapshot(published_dir, clock, current)

    return BriefResult(
        out_path=str(out_path),
        snapshot_path=str(snapshot_path),
        counts=diff.counts(),
        no_material_change=not diff.has_material_change,
    )
