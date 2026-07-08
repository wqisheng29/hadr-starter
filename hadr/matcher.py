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

from .fetch import USGS_SOURCE
from .model import QuakeRecord


def resolve_canonical_id(conn: sqlite3.Connection, record: QuakeRecord) -> str:
    """Find the canonical_id for this record, or mint a new one.

    Deterministic: on a fresh DB the same fixture always yields the same
    ids -> the same minted canonical_id.
    """
    placeholders = ",".join("?" for _ in record.ids)
    if record.ids:
        rows = conn.execute(
            f"SELECT DISTINCT canonical_id FROM feed_identifiers "
            f"WHERE source = ? AND feed_id IN ({placeholders})",
            (USGS_SOURCE, *sorted(record.ids)),
        ).fetchall()
        existing = {row[0] for row in rows}
        if existing:
            # Slice 1 sees at most one; sorted() keeps the pick deterministic
            # if a future merge ever surfaces more than one here.
            return sorted(existing)[0]

    return mint_canonical_id(record)


def mint_canonical_id(record: QuakeRecord) -> str:
    return f"{USGS_SOURCE}:{record.preferred_id}"
