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

from . import config
from .clock import Clock
from .fetch import GDACS_SOURCE, GLIDE_SOURCE, USGS_SOURCE
from .matcher import resolve_canonical_id, resolve_canonical_id_gdacs
from .model import EventRow, GdacsRecord, QuakeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_events (
    canonical_id            TEXT PRIMARY KEY,
    hazard_type             TEXT NOT NULL DEFAULT 'earthquake',
    status                  TEXT,
    first_seen              TEXT NOT NULL,
    last_updated            TEXT NOT NULL,
    magnitude               REAL,
    depth_km                REAL,
    place                   TEXT,
    title                   TEXT,
    origin_time             TEXT,
    longitude               REAL,
    latitude                REAL,
    usgs_preferred_id       TEXT,
    usgs_ids                TEXT,
    gdacs_eventid           TEXT,
    gdacs_episodeid         TEXT,
    gdacs_alertlevel        TEXT,
    gdacs_episodealertlevel TEXT,
    glide                   TEXT,
    country                 TEXT
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

# GDACS-derived columns. Reconciling GDACS touches only these — never the
# USGS-owned fields above — so a corroborating GDACS record cannot clobber the
# authoritative USGS magnitude/place/title on a combined event.
_GDACS_TRACKED = ("gdacs_eventid", "gdacs_episodeid", "gdacs_alertlevel",
                  "gdacs_episodealertlevel", "glide", "country")

# GDACS event-type code -> canonical hazard_type. Earthquakes only this slice.
_HAZARD_BY_EVENTTYPE = {"EQ": "earthquake"}


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


def reconcile_gdacs(
    conn: sqlite3.Connection, records: list[GdacsRecord], clock: Clock
) -> int:
    """Fold GDACS records onto the ledger. Returns rows inserted or updated.

    A record that resolves to an existing (USGS) canonical event updates only the
    GDACS-derived columns; USGS-owned fields are left untouched. A GDACS-only
    event (no matching row) is inserted from the GDACS data. ``last_updated`` is
    bumped only when a tracked GDACS field actually changes, so a re-run is a
    true no-op.
    """
    now = clock.now().isoformat()
    changed = 0
    for record in records:
        canonical_id = resolve_canonical_id_gdacs(conn, record)
        existing = conn.execute(
            "SELECT * FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        target = _gdacs_target_fields(record, existing)

        if existing is None:
            conn.execute(
                "INSERT INTO canonical_events "
                "(canonical_id, hazard_type, status, first_seen, last_updated, "
                " magnitude, depth_km, place, title, origin_time, longitude, latitude, "
                f" {', '.join(_GDACS_TRACKED)}) "
                "VALUES (?, ?, 'provisional', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                f"{', '.join('?' for _ in _GDACS_TRACKED)})",
                (canonical_id, _hazard_type(record.eventtype), now, now,
                 record.magnitude, record.depth_km, record.name, record.name,
                 _iso(record.origin_time_ms), record.longitude, record.latitude,
                 *(target[f] for f in _GDACS_TRACKED)),
            )
            changed += 1
        elif any(existing[f] != target[f] for f in _GDACS_TRACKED):
            conn.execute(
                f"UPDATE canonical_events SET last_updated = ?, "
                f"{', '.join(f'{f} = ?' for f in _GDACS_TRACKED)} WHERE canonical_id = ?",
                (now, *(target[f] for f in _GDACS_TRACKED), canonical_id),
            )
            changed += 1
        # else: identical GDACS columns -> no write (keeps re-runs idempotent).

        _link_gdacs_ids(conn, record, canonical_id)

    conn.commit()
    return changed


def _hazard_type(eventtype: str) -> str:
    return _HAZARD_BY_EVENTTYPE.get(eventtype, eventtype.lower())


def _gdacs_target_fields(record: GdacsRecord, existing) -> dict[str, object]:
    """Compute the target GDACS columns, folding in the existing row.

    ``gdacs_alertlevel`` is monotonic (event-MAX severity ever seen);
    ``gdacs_episodealertlevel`` is always the latest. ``glide``/``country`` keep
    a prior non-null value when the incoming record omits it.
    """
    def prev(col: str):
        return existing[col] if existing is not None else None

    return {
        "gdacs_eventid": record.eventid,
        "gdacs_episodeid": record.episodeid or prev("gdacs_episodeid"),
        "gdacs_alertlevel": _alert_max(prev("gdacs_alertlevel"), record.alertlevel),
        "gdacs_episodealertlevel": record.episodealertlevel,
        "glide": (record.glide or None) or prev("glide"),
        "country": record.country if record.country is not None else prev("country"),
    }


def _alert_max(current: str | None, incoming: str | None) -> str | None:
    """The higher-severity of two GDACS alert levels (green < orange < red)."""
    if incoming is None:
        return current
    if current is None:
        return incoming
    rank = config.GDACS_ALERT_RANK
    return incoming if rank.get(incoming.lower(), -1) > rank.get(current.lower(), -1) else current


def _link_gdacs_ids(
    conn: sqlite3.Connection, record: GdacsRecord, canonical_id: str
) -> None:
    """Link every identifier this GDACS record carries to the canonical event.

    Includes the crosswalk row (``usgs``, ``sourceid``) for US/NEIC quakes, so a
    NEIC ``sourceid`` collapses onto the USGS canonical event.
    """
    links = [(GDACS_SOURCE, f"{record.eventtype}:{record.eventid}", canonical_id)]
    if record.glide:
        links.append((GLIDE_SOURCE, record.glide, canonical_id))
    if record.source.lower() in config.NEIC_SOURCES and record.sourceid:
        links.append((USGS_SOURCE, record.sourceid, canonical_id))
    conn.executemany(
        "INSERT OR IGNORE INTO feed_identifiers (source, feed_id, canonical_id) "
        "VALUES (?, ?, ?)",
        links,
    )


def read_events(conn: sqlite3.Connection) -> list[EventRow]:
    """All canonical events, ordered for display (strongest first).

    Each row reports the distinct feeds that corroborate it (``sources``), so the
    dashboard can tag a quake seen by more than one feed. Deduped by canonical_id
    (one row each) regardless of how many feed identifiers point at it.
    """
    rows = conn.execute(
        "SELECT canonical_id, title, magnitude, place, origin_time, gdacs_episodealertlevel "
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
            sources=_sources_for(conn, row["canonical_id"]),
            gdacs_episodealertlevel=row["gdacs_episodealertlevel"],
        )
        for row in rows
    ]


def _sources_for(conn: sqlite3.Connection, canonical_id: str) -> tuple[str, ...]:
    """The feeds that corroborate this event. GLIDE is a cross-feed disaster
    number, not a feed, so it is excluded — only real sources (usgs, gdacs, …)
    count toward "how many feeds vouch for this quake"."""
    rows = conn.execute(
        "SELECT DISTINCT source FROM feed_identifiers "
        "WHERE canonical_id = ? AND source != ? ORDER BY source",
        (canonical_id, GLIDE_SOURCE),
    ).fetchall()
    return tuple(row[0] for row in rows)
