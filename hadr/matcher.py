"""Cross-feed identity — trivial in slice 1, but the seam is real.

A canonical event is addressed by an *assigned surrogate* ``canonical_id``,
never a raw feed id. Identity resolution goes through the ``feed_identifiers``
table: any of a record's ids already mapped -> reuse that canonical event;
otherwise mint a new one.

Today only USGS flows through here, so this just keeps a quake stable across
preferred-id changes (union-of-ids). In slice 2 the same lookup is what
collapses a GDACS record whose ``sourceid`` equals a USGS id onto the same
canonical event — no shape change required.
"""

import sqlite3

from . import config
from .fetch import GDACS_SOURCE, GLIDE_SOURCE, USGS_SOURCE
from .model import GdacsRecord, QuakeRecord


def resolve_canonical_id(conn: sqlite3.Connection, record: QuakeRecord) -> str:
    """Find the canonical_id for this record, or mint a new one.

    Deterministic: on a fresh DB the same fixture always yields the same
    ids -> the same minted canonical_id.
    """
    if record.ids:
        existing = _lookup(conn, USGS_SOURCE, sorted(record.ids))
        if existing:
            # Slice 1 sees at most one; sorted() keeps the pick deterministic
            # if a future merge ever surfaces more than one here.
            return sorted(existing)[0]

    return mint_canonical_id(record)


def mint_canonical_id(record: QuakeRecord) -> str:
    return f"{USGS_SOURCE}:{record.preferred_id}"


def resolve_canonical_id_gdacs(conn: sqlite3.Connection, record: GdacsRecord) -> str:
    """Resolve a GDACS record onto an existing canonical event, or mint one.

    Deterministic, in strict priority order — the FIRST tier that finds an
    existing canonical_id wins (within a tier, ``sorted(...)[0]`` breaks any tie;
    a full merge of two pre-existing events is out of slice-2 scope):

    (a) source/sourceid crosswalk — a US/NEIC-detected quake is the same physical
        event USGS already carries, so it attaches to that ``usgs:...`` id;
    (b) GLIDE — a shared disaster number links records across feeds;
    (c) GDACS native key ``{eventtype}:{eventid}`` — stable across episodes.

    None match -> mint ``gdacs:{eventtype}:{eventid}``.
    """
    if record.source.lower() in config.NEIC_SOURCES and record.sourceid:
        crosswalk = _lookup(conn, USGS_SOURCE, [record.sourceid])
        if crosswalk:
            return sorted(crosswalk)[0]

    if record.glide:
        by_glide = _lookup(conn, GLIDE_SOURCE, [record.glide])
        if by_glide:
            return sorted(by_glide)[0]

    native = _lookup(conn, GDACS_SOURCE, [_gdacs_native_key(record)])
    if native:
        return sorted(native)[0]

    return mint_canonical_id_gdacs(record)


def mint_canonical_id_gdacs(record: GdacsRecord) -> str:
    return f"{GDACS_SOURCE}:{_gdacs_native_key(record)}"


def _gdacs_native_key(record: GdacsRecord) -> str:
    """GDACS's own stable key for an event, independent of episode."""
    return f"{record.eventtype}:{record.eventid}"


def _lookup(conn: sqlite3.Connection, source: str, feed_ids: list[str]) -> set[str]:
    if not feed_ids:
        return set()
    placeholders = ",".join("?" for _ in feed_ids)
    rows = conn.execute(
        f"SELECT DISTINCT canonical_id FROM feed_identifiers "
        f"WHERE source = ? AND feed_id IN ({placeholders})",
        (source, *feed_ids),
    ).fetchall()
    return {row[0] for row in rows}
