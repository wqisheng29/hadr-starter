"""The cross-feed crosswalk: a GDACS record resolves onto the right canonical
event via NEIC sourceid, GLIDE, or its own native key — deterministically."""

from hadr.ledger import connect, reconcile, reconcile_gdacs
from hadr.matcher import resolve_canonical_id_gdacs
from hadr.model import GdacsRecord, QuakeRecord


def _quake(preferred_id, ids, mag=6.8):
    return QuakeRecord(
        preferred_id=preferred_id, ids=frozenset(ids), magnitude=mag,
        place="p", title="t", origin_time_ms=1783300000000,
        updated_ms=1783300000000, longitude=99.6, latitude=-1.4, depth_km=30.0,
    )


def _gdacs(eventid, *, source="NEIC", sourceid="", glide="", episodeid="ep",
           alertlevel="Orange", mag=6.8):
    return GdacsRecord(
        eventtype="EQ", eventid=eventid, episodeid=episodeid, glide=glide,
        name="Quake", alertlevel=alertlevel, episodealertlevel=alertlevel,
        alertscore=1.5, episodealertscore=1.5, country="Indonesia", iso3="IDN",
        source=source, sourceid=sourceid, magnitude=mag,
        origin_time_ms=1783300000000, longitude=99.6, latitude=-1.4, depth_km=None,
    )


def test_neic_sourceid_crosswalk_maps_onto_usgs_event(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        # USGS is the detection layer: seed it first.
        reconcile(conn, [_quake("us7000abcd", {"us7000abcd", "at00abcd"})], frozen_clock)
        rec = _gdacs("1550999", source="NEIC", sourceid="us7000abcd")
        assert resolve_canonical_id_gdacs(conn, rec) == "usgs:us7000abcd"
    finally:
        conn.close()


def test_glide_crosswalk_collapses_two_gdacs_records(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        first = _gdacs("1551200", source="IDC", sourceid="idc1", glide="EQ-2026-1-IDN")
        second = _gdacs("1551201", source="GFZ", sourceid="gfz1", glide="EQ-2026-1-IDN")
        reconcile_gdacs(conn, [first], frozen_clock)
        first_id = resolve_canonical_id_gdacs(conn, first)
        # The second, different eventid + source, still lands on the first via GLIDE.
        assert resolve_canonical_id_gdacs(conn, second) == first_id
        reconcile_gdacs(conn, [second], frozen_clock)
        assert conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_shared_glide_group_is_idempotent_and_folds_alert_max(frozen_clock, tmp_ledger):
    # Two records sharing a GLIDE collapse to ONE canonical event, written once;
    # re-running the identical payload writes nothing (no last_updated churn), and
    # gdacs_alertlevel is the event-max across the group.
    conn = connect(tmp_ledger)
    try:
        first = _gdacs("1551200", source="IDC", sourceid="idc1",
                       glide="EQ-2026-1-IDN", alertlevel="Green")
        second = _gdacs("1551201", source="GFZ", sourceid="gfz1",
                        glide="EQ-2026-1-IDN", alertlevel="Orange")

        assert reconcile_gdacs(conn, [first, second], frozen_clock) == 1  # one event, one write
        assert conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0] == 1
        assert reconcile_gdacs(conn, [first, second], frozen_clock) == 0  # idempotent re-run

        row = conn.execute(
            "SELECT gdacs_alertlevel, gdacs_episodealertlevel FROM canonical_events"
        ).fetchone()
        assert row["gdacs_alertlevel"] == "Orange"        # event-max folded across the group
        assert row["gdacs_episodealertlevel"] == "Orange"  # latest episode

        # Order-independent: reconciling the reversed payload leaves the stored
        # columns unchanged (still a no-op).
        assert reconcile_gdacs(conn, [second, first], frozen_clock) == 0
    finally:
        conn.close()


def test_gdacs_native_key_stable_across_episodes(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        ep1 = _gdacs("1553000", source="GFZ", sourceid="gfz2", episodeid="2000")
        reconcile_gdacs(conn, [ep1], frozen_clock)
        canonical = resolve_canonical_id_gdacs(conn, ep1)
        assert canonical == "gdacs:EQ:1553000"
        # A later episode of the SAME eventid resolves to the same canonical id.
        ep2 = _gdacs("1553000", source="GFZ", sourceid="gfz2", episodeid="2001")
        assert resolve_canonical_id_gdacs(conn, ep2) == canonical
    finally:
        conn.close()


def test_resolution_is_deterministic(frozen_clock, tmp_ledger):
    conn = connect(tmp_ledger)
    try:
        reconcile(conn, [_quake("us7000abcd", {"us7000abcd"})], frozen_clock)
        rec = _gdacs("1550999", source="NEIC", sourceid="us7000abcd")
        first = resolve_canonical_id_gdacs(conn, rec)
        second = resolve_canonical_id_gdacs(conn, rec)
        assert first == second == "usgs:us7000abcd"
    finally:
        conn.close()
