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
    country                 TEXT,
    usgs_status             TEXT,
    pager_alert             TEXT,
    gdacs_is_temporary      INTEGER
);

CREATE TABLE IF NOT EXISTS feed_identifiers (
    source        TEXT NOT NULL,
    feed_id       TEXT NOT NULL,
    canonical_id  TEXT NOT NULL REFERENCES canonical_events(canonical_id),
    PRIMARY KEY (source, feed_id)
);
"""

# Fields compared to decide whether an UPDATE (and last_updated bump) is needed.
# ``usgs_status``/``pager_alert`` are lifecycle signals (Slice 3): a change bumps
# last_updated, and being tracked keeps a re-run a true no-op.
_TRACKED = ("magnitude", "depth_km", "place", "title", "origin_time",
            "longitude", "latitude", "usgs_preferred_id", "usgs_ids",
            "usgs_status", "pager_alert")

# GDACS-derived columns. Reconciling GDACS touches only these — never the
# USGS-owned fields above — so a corroborating GDACS record cannot clobber the
# authoritative USGS magnitude/place/title on a combined event.
# ``gdacs_is_temporary`` (0/1) is the GDACS settle signal (Slice 3).
_GDACS_TRACKED = ("gdacs_eventid", "gdacs_episodeid", "gdacs_alertlevel",
                  "gdacs_episodealertlevel", "glide", "country",
                  "gdacs_is_temporary")

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
        "usgs_status": record.status,
        "pager_alert": record.pager_alert,
    }


def _has_confirming_signal(
    usgs_status: str | None, pager_alert: str | None, gdacs_is_temporary: object
) -> bool:
    """True if any feed-native confirming signal is present (Slice 3). Inputs are
    the stored columns; NO enrichment fetch — confirmation reflects only what the
    feeds already carried. Thresholds/sets live in ``config``."""
    if usgs_status is not None and usgs_status.lower() == config.CONFIRMED_USGS_STATUS:
        return True
    if pager_alert:  # a non-null/non-empty PAGER colour is a settled impact signal
        return True
    if gdacs_is_temporary is not None and int(gdacs_is_temporary) == 0:
        return True  # GDACS istemporary flipped off -> settled (e.g. ShakeMap)
    return False


def _lifecycle_status(
    current: str | None,
    usgs_status: str | None,
    pager_alert: str | None,
    gdacs_is_temporary: object,
) -> str:
    """The lifecycle status given a row's current status and stored signals.
    Confirmation is STICKY: once confirmed, never regresses to provisional."""
    if current == config.STATUS_CONFIRMED:
        return config.STATUS_CONFIRMED
    if _has_confirming_signal(usgs_status, pager_alert, gdacs_is_temporary):
        return config.STATUS_CONFIRMED
    return config.STATUS_PROVISIONAL


def _refresh_status(conn: sqlite3.Connection, canonical_id: str, now: str) -> bool:
    """Recompute a canonical event's lifecycle status from its stored signals and
    persist it if it changed (bumping ``last_updated`` only then — a no-op leaves
    the row untouched, so re-runs don't thrash it). Called from BOTH reconcile
    paths so either feed's settle promotes the event. Returns True on change.

    Deliberately not counted toward ``rows_written``: it is a derived consequence
    of a feed update already reflected in the tracked-field counts, not a second
    logical write."""
    row = conn.execute(
        "SELECT status, usgs_status, pager_alert, gdacs_is_temporary "
        "FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
    ).fetchone()
    if row is None:
        return False
    new_status = _lifecycle_status(
        row["status"], row["usgs_status"], row["pager_alert"], row["gdacs_is_temporary"]
    )
    if new_status == row["status"]:
        return False
    conn.execute(
        "UPDATE canonical_events SET status = ?, last_updated = ? WHERE canonical_id = ?",
        (new_status, now, canonical_id),
    )
    return True


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
                f"VALUES (?, ?, ?, ?, {', '.join('?' for _ in _TRACKED)})",
                (canonical_id, config.STATUS_PROVISIONAL, now, now,
                 *(fields[f] for f in _TRACKED)),
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
        _refresh_status(conn, canonical_id, now)

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

    # Pass 1 — resolve, ensure a row exists, link, and group. Several GDACS records
    # can collapse onto one canonical event (an aftershock sequence sharing a GLIDE,
    # or two episodes of one eventid in the same payload). Writing per-record would
    # let them overwrite each other's columns and re-fire the UPDATE on every
    # re-run (last_updated churn under a real clock); instead each group folds to a
    # single deterministic target and is written once in pass 2, so a plain re-run
    # is a true no-op. A newly-minted GDACS-only event gets a base row inserted here
    # (from the first record that mints it) so identifier links satisfy the FK and a
    # later record in the payload can find it by GLIDE; its GDACS columns are filled
    # by the fold in pass 2.
    groups: dict[str, list[GdacsRecord]] = {}
    created: set[str] = set()
    for record in records:
        canonical_id = resolve_canonical_id_gdacs(conn, record)
        first_in_payload = canonical_id not in groups
        groups.setdefault(canonical_id, []).append(record)
        if first_in_payload and conn.execute(
            "SELECT 1 FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
        ).fetchone() is None:
            conn.execute(
                "INSERT INTO canonical_events "
                "(canonical_id, hazard_type, status, first_seen, last_updated, "
                " magnitude, depth_km, place, title, origin_time, longitude, latitude) "
                "VALUES (?, ?, 'provisional', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (canonical_id, _hazard_type(record.eventtype), now, now,
                 record.magnitude, record.depth_km, record.name, record.name,
                 _iso(record.origin_time_ms), record.longitude, record.latitude),
            )
            created.add(canonical_id)
        _link_gdacs_ids(conn, record, canonical_id)

    # Pass 2 — fold each group's GDACS columns and write once.
    changed = 0
    for canonical_id, group in groups.items():
        row = conn.execute(
            "SELECT * FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        prior = None if canonical_id in created else row
        target = _gdacs_target_fields(group, prior)
        if canonical_id in created or any(row[f] != target[f] for f in _GDACS_TRACKED):
            conn.execute(
                f"UPDATE canonical_events SET last_updated = ?, "
                f"{', '.join(f'{f} = ?' for f in _GDACS_TRACKED)} WHERE canonical_id = ?",
                (now, *(target[f] for f in _GDACS_TRACKED), canonical_id),
            )
            changed += 1
        # else: identical GDACS columns -> no write (keeps re-runs idempotent).
        _refresh_status(conn, canonical_id, now)

    conn.commit()
    return changed


def _hazard_type(eventtype: str) -> str:
    return _HAZARD_BY_EVENTTYPE.get(eventtype, eventtype.lower())


def _latest_gdacs(group: list[GdacsRecord]) -> GdacsRecord:
    """The latest record in a group, chosen deterministically (independent of
    payload order): newest origin time, then episodeid, then eventid. Its
    episode-level fields (eventid, episodeid, episodealertlevel) become the
    event's current values."""
    return max(group, key=lambda r: (r.origin_time_ms, r.episodeid, r.eventid))


def _gdacs_target_fields(group: list[GdacsRecord], existing) -> dict[str, object]:
    """Fold a group of GDACS records (all resolving to one canonical event) plus
    the existing row into the target GDACS columns.

    ``gdacs_alertlevel`` is monotonic (event-MAX severity ever seen across the
    whole group and the prior row); ``gdacs_episodealertlevel``/``eventid``/
    ``episodeid`` come from the latest episode in the group;
    ``glide``/``country`` keep a prior non-null value when none is supplied.
    Order-independent, so a re-run of the same payload is a true no-op.
    """
    def prev(col: str):
        return existing[col] if existing is not None else None

    latest = _latest_gdacs(group)
    alert_max = prev("gdacs_alertlevel")
    for record in group:
        alert_max = _alert_max(alert_max, record.alertlevel)
    glide = next((r.glide for r in group if r.glide), None) or prev("glide")
    country = next((r.country for r in group if r.country is not None), prev("country"))

    return {
        "gdacs_eventid": latest.eventid,
        "gdacs_episodeid": latest.episodeid or prev("gdacs_episodeid"),
        "gdacs_alertlevel": alert_max,
        "gdacs_episodealertlevel": latest.episodealertlevel,
        "glide": glide,
        "country": country,
        # Latest episode's settle flag, stored 0/1 so the idempotency comparison
        # round-trips cleanly through SQLite INTEGER (mirrors the care elsewhere).
        "gdacs_is_temporary": 0 if not latest.is_temporary else 1,
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
        "SELECT canonical_id, title, magnitude, place, origin_time, "
        "gdacs_episodealertlevel, status, pager_alert "
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
            status=row["status"],
            pager_alert=row["pager_alert"],
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
