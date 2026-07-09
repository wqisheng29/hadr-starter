"""Regression tests for Slice 6 review findings.

F1: a ReliefWeb link must not exempt an event from aged-out/retract forever.
F2: a non-http(s) ReliefWeb url is dropped (no javascript:/data: XSS link).
F3: a model that RAISES degrades to the deterministic basis, never crashes.
"""

from datetime import datetime, timezone

from hadr import config
from hadr.brief import _impact_basis
from hadr.clock import FrozenClock
from hadr.diff import SnapshotEvent
from hadr.ledger import (
    apply_link_decisions,
    connect,
    reconcile,
    reconcile_absences,
)
from hadr.link_decisions import LinkDecision
from hadr.model import QuakeRecord
from hadr.reliefweb import ReliefWebRecord


def _quake(pid="us1", mag=6.0, origin_ms=1748736000000):  # 2025-06-01, well past 72h
    return QuakeRecord(
        preferred_id=pid, ids=frozenset({pid}), magnitude=mag, place="p", title="t",
        origin_time_ms=origin_ms, updated_ms=origin_ms, longitude=1.0, latitude=2.0,
        depth_km=10.0, status="reviewed", pager_alert=None,
    )


def test_reliefweb_link_does_not_make_event_immortal(tmp_ledger):
    # F1: an event linked to a ReliefWeb item, absent from a reachable USGS tick,
    # must still age out — ReliefWeb (never polled) must not pin it active.
    clock = FrozenClock(datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc))
    conn = connect(tmp_ledger)
    try:
        reconcile(conn, [_quake("us1")], clock)
        apply_link_decisions(conn, [LinkDecision("rw1", "usgs:us1")])
        # 'reliefweb' now vouches for usgs:us1, but the tick only polled USGS and
        # did not see us1 this run:
        changed = reconcile_absences(conn, seen=set(), reachable_sources={"usgs"}, clock=clock)
        status = conn.execute(
            "SELECT status FROM canonical_events WHERE canonical_id='usgs:us1'"
        ).fetchone()[0]
        assert changed == 1
        assert status in (config.STATUS_AGED_OUT, config.STATUS_RETRACTED)  # not immortal
    finally:
        conn.close()


def test_reliefweb_url_scheme_is_validated():
    # F2: javascript:/data: schemes are dropped to "" (no clickable XSS link);
    # http(s) is preserved.
    assert ReliefWebRecord.from_dict({"id": "a", "url": "javascript:alert(1)"}).url == ""
    assert ReliefWebRecord.from_dict({"id": "b", "url": "data:text/html,x"}).url == ""
    good = "https://reliefweb.int/report/x"
    assert ReliefWebRecord.from_dict({"id": "c", "url": good}).url == good


def test_impact_basis_degrades_when_model_raises():
    # F3: a model whose complete() raises degrades to the deterministic basis.
    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("model exploded")

    e = SnapshotEvent(
        canonical_id="usgs:us1", title="t", magnitude=6.8, place="p",
        status="confirmed", gdacs_episodealertlevel="Red", pager_alert="orange",
        origin_time=None, sources=("usgs",), tier=2,
    )
    basis = _impact_basis(Boom(), e, [])
    assert basis and "Red" in basis  # deterministic fallback rendered, no crash
