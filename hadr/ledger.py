"""The ledger: SQLite store of canonical events (ADR-0003, ADR-0006).

Slice 1 does insert-or-update only. A quake that vanishes from a later fetch is
*left as-is*, never deleted — retraction/aged-out is deliberately slice 3+.

Idempotency rests on ``reconcile`` doing a true no-op when nothing changed:
``last_updated`` is bumped only when a tracked field actually differs, so a
plain re-run touches no rows even under a real (non-frozen) clock.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .clock import Clock
from .fetch import USGS_SOURCE
from .matcher import resolve_canonical_id
from .model import EventRow, QuakeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_events (
    canonical_id      TEXT PRIMARY KEY,
    hazard_type       TEXT NOT NULL DEFAULT 'earthquake',
    status            TEXT,
    first_seen        TEXT NOT NULL,
    last_updated      TEXT NOT NULL,
    magnitude         REAL,
    depth_km          REAL,
    place             TEXT,
    title             TEXT,
    origin_time       TEXT,
    longitude         REAL,
    latitude          REAL,
    usgs_preferred_id TEXT,
    usgs_ids          TEXT
);

CREATE TABLE IF NOT EXISTS feed_identifiers (
    source        TEXT NOT NULL,
    feed_id       TEXT NOT NULL,
    canonical_id  TEXT NOT NULL REFERENCES canonical_events(canonical_id),
    PRIMARY KEY (source, feed_id)
);
"""

# Fields compared to decide whether an UPDATE (and last_updated bump) is needed.
_TRACKED = ("magnitude", "depth_km", "place", "title", "origin_time",
            "longitude", "latitude", "usgs_preferred_id", "usgs_ids")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating parent dirs) and ensure the schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _fields_from(record: QuakeRecord) -> dict[str, object]:
    return {
        "magnitude": record.magnitude,
        "depth_km": record.depth_km,
        "place": record.place,
        "title": record.title,
        "origin_time": _iso(record.origin_time_ms),
        "longitude": record.longitude,
        "latitude": record.latitude,
        "usgs_preferred_id": record.preferred_id,
        "usgs_ids": json.dumps(sorted(record.ids)),
    }


def reconcile(conn: sqlite3.Connection, records: list[QuakeRecord], clock: Clock) -> int:
    """Upsert each record. Returns the number of rows inserted or updated."""
    now = clock.now().isoformat()
    changed = 0
    for record in records:
        canonical_id = resolve_canonical_id(conn, record)
        fields = _fields_from(record)
        existing = conn.execute(
            "SELECT * FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                f"INSERT INTO canonical_events "
                f"(canonical_id, status, first_seen, last_updated, {', '.join(_TRACKED)}) "
                f"VALUES (?, 'provisional', ?, ?, {', '.join('?' for _ in _TRACKED)})",
                (canonical_id, now, now, *(fields[f] for f in _TRACKED)),
            )
            changed += 1
        elif any(existing[f] != fields[f] for f in _TRACKED):
            conn.execute(
                f"UPDATE canonical_events SET last_updated = ?, "
                f"{', '.join(f'{f} = ?' for f in _TRACKED)} WHERE canonical_id = ?",
                (now, *(fields[f] for f in _TRACKED), canonical_id),
            )
            changed += 1
        # else: identical -> no write (keeps re-runs idempotent).

        _link_ids(conn, record.ids, canonical_id)

    conn.commit()
    return changed


def _link_ids(conn: sqlite3.Connection, ids: frozenset[str], canonical_id: str) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO feed_identifiers (source, feed_id, canonical_id) "
        "VALUES (?, ?, ?)",
        [(USGS_SOURCE, feed_id, canonical_id) for feed_id in sorted(ids)],
    )


def read_events(conn: sqlite3.Connection) -> list[EventRow]:
    """All canonical events, ordered for display (strongest first)."""
    rows = conn.execute(
        "SELECT canonical_id, title, magnitude, place, origin_time "
        "FROM canonical_events "
        "ORDER BY magnitude DESC NULLS LAST, canonical_id ASC"
    ).fetchall()
    return [
        EventRow(
            canonical_id=row["canonical_id"],
            title=row["title"],
            magnitude=row["magnitude"],
            place=row["place"],
            origin_time=row["origin_time"],
        )
        for row in rows
    ]
