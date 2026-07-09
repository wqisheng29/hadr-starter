"""Ledger ordering determinism: equal-magnitude events must break ties on
canonical_id so the rendered dashboard is byte-identical across runs. Every
shipped fixture uses distinct magnitudes, so without this test a regression that
dropped the tie-break would pass the whole suite while making output unstable.
"""

from hadr.ledger import connect, read_events, reconcile
from hadr.model import QuakeRecord


def _rec(preferred_id, mag):
    return QuakeRecord(
        preferred_id=preferred_id,
        ids=frozenset({preferred_id}),
        magnitude=mag,
        place="p",
        title="t",
        origin_time_ms=1783300000000,
        updated_ms=1783300000000,
        longitude=1.0,
        latitude=2.0,
        depth_km=10.0,
    )


def test_equal_magnitude_orders_by_canonical_id(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        # Insert out of canonical_id order; all share magnitude 5.0.
        reconcile(
            conn,
            [_rec("zzz", 5.0), _rec("aaa", 5.0), _rec("mmm", 5.0)],
            frozen_clock,
        )
        order = [e.canonical_id for e in read_events(conn)]
        assert order == ["usgs:aaa", "usgs:mmm", "usgs:zzz"]
    finally:
        conn.close()
