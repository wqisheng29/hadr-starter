"""Unit tests for the ledger's upsert / idempotency contract."""

from hadr.clock import FrozenClock
from hadr.ledger import connect, read_events, reconcile
from hadr.model import QuakeRecord


def _rec(preferred_id, ids, mag, title="t", place="p", origin=1783300000000):
    return QuakeRecord(
        preferred_id=preferred_id,
        ids=frozenset(ids),
        magnitude=mag,
        place=place,
        title=title,
        origin_time_ms=origin,
        updated_ms=origin,
        longitude=1.0,
        latitude=2.0,
        depth_km=10.0,
    )


def test_insert_then_noop(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        recs = [_rec("us1", {"us1"}, 5.0)]
        assert reconcile(conn, recs, frozen_clock) == 1  # inserted
        assert reconcile(conn, recs, frozen_clock) == 0  # identical -> no-op
        assert len(read_events(conn)) == 1
    finally:
        conn.close()


def test_update_bumps_last_updated_only_on_change(tmp_ledger):
    from datetime import datetime, timezone

    early = FrozenClock(datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc))
    late = FrozenClock(datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc))
    conn = connect(tmp_ledger)
    try:
        reconcile(conn, [_rec("us1", {"us1"}, 5.0)], early)
        first = conn.execute(
            "SELECT first_seen, last_updated FROM canonical_events"
        ).fetchone()

        # Re-run with a later clock but identical data -> last_updated must NOT move.
        reconcile(conn, [_rec("us1", {"us1"}, 5.0)], late)
        unchanged = conn.execute("SELECT last_updated FROM canonical_events").fetchone()
        assert unchanged[0] == first[1]

        # Now a real magnitude change -> last_updated advances, first_seen stays.
        assert reconcile(conn, [_rec("us1", {"us1"}, 5.4)], late) == 1
        after = conn.execute(
            "SELECT first_seen, last_updated, magnitude FROM canonical_events"
        ).fetchone()
        assert after[0] == first[0]           # first_seen preserved
        assert after[1] != first[1]           # last_updated bumped
        assert after[2] == 5.4
    finally:
        conn.close()


def test_union_of_ids_reuses_canonical(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        reconcile(conn, [_rec("us1", {"us1", "ci9"}, 5.0)], frozen_clock)
        # A later tick with a new preferred id but an overlapping id.
        reconcile(conn, [_rec("ci9", {"ci9", "at7"}, 5.0)], frozen_clock)
        assert conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0] == 1
        canonical = conn.execute("SELECT canonical_id FROM canonical_events").fetchone()[0]
        assert canonical == "usgs:us1"  # minted from the first sighting, immutable
    finally:
        conn.close()
