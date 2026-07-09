"""Seam 2, the deterministic core: recorded, overridable ReliefWeb<->event link
decisions. NO model in these assertions — the model only PRODUCES a decision
(tested in test_brief_model.py); here we prove that applying a recorded decision
links the right canonical event, that re-applying is a no-op, and that OVERRIDING
re-links on the next apply (PRD user story 36 / scenario 12).

The fast tick (``pipeline.run``, sole writer of ledger.db) applies decisions from
the disjoint file; the pure ``ledger.apply_link_decisions`` is the seam under test.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from hadr.clock import FrozenClock
from hadr.fetch import FixtureFeedSource, RELIEFWEB_SOURCE
from hadr.ledger import (
    apply_link_decisions,
    connect,
    reconcile,
    read_events,
    reliefweb_links,
)
from hadr.link_decisions import LinkDecision, load_decisions, write_decisions
from hadr.model import QuakeRecord
from hadr.pipeline import run

_FIX = Path(__file__).resolve().parents[1] / "fixtures"
_CLOCK = FrozenClock(datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc))


def _quake(preferred_id: str, mag: float = 6.5) -> QuakeRecord:
    return QuakeRecord(
        preferred_id=preferred_id, ids=frozenset({preferred_id}), magnitude=mag,
        place="p", title=f"M{mag} quake", origin_time_ms=1783463400000,
        updated_ms=1783463400000, longitude=99.0, latitude=-1.0, depth_km=30.0,
    )


def _seed_two(tmp_path) -> Path:
    db = tmp_path / "ledger.db"
    conn = connect(db)
    try:
        reconcile(conn, [_quake("us7000aaa"), _quake("us7000bbb")], _CLOCK)
    finally:
        conn.close()
    return db


def _feed_id_rows(db, reliefweb_id):
    conn = connect(db)
    try:
        return conn.execute(
            "SELECT canonical_id FROM feed_identifiers WHERE source = ? AND feed_id = ?",
            (RELIEFWEB_SOURCE, reliefweb_id),
        ).fetchall()
    finally:
        conn.close()


# --- apply records the link ---------------------------------------------------


def test_apply_links_reliefweb_to_canonical_event(tmp_path):
    db = _seed_two(tmp_path)
    conn = connect(db)
    try:
        n = apply_link_decisions(conn, [LinkDecision("rw-1", "usgs:us7000aaa")])
        assert n == 1
        assert reliefweb_links(conn) == {"rw-1": "usgs:us7000aaa"}
        # The linked event now reports ReliefWeb among its corroborating sources.
        ev = next(e for e in read_events(conn) if e.canonical_id == "usgs:us7000aaa")
        assert RELIEFWEB_SOURCE in ev.sources
    finally:
        conn.close()


def test_apply_is_idempotent(tmp_path):
    db = _seed_two(tmp_path)
    conn = connect(db)
    try:
        d = [LinkDecision("rw-1", "usgs:us7000aaa")]
        assert apply_link_decisions(conn, d) == 1
        assert apply_link_decisions(conn, d) == 0   # re-apply writes nothing
        assert reliefweb_links(conn) == {"rw-1": "usgs:us7000aaa"}
    finally:
        conn.close()


def test_override_relinks_on_next_apply(tmp_path):
    """The load-bearing property: a later decision mapping the SAME ReliefWeb id
    to a DIFFERENT event re-points the link."""
    db = _seed_two(tmp_path)
    conn = connect(db)
    try:
        apply_link_decisions(conn, [LinkDecision("rw-1", "usgs:us7000aaa")])
        n = apply_link_decisions(conn, [LinkDecision("rw-1", "usgs:us7000bbb", method="override")])
        assert n == 1
        assert reliefweb_links(conn) == {"rw-1": "usgs:us7000bbb"}   # re-pointed
        # Exactly one row for this ReliefWeb id — the override replaced, not added.
        assert len(_feed_id_rows(db, "rw-1")) == 1
    finally:
        conn.close()


def test_decision_for_unknown_event_is_skipped_not_crash(tmp_path):
    db = _seed_two(tmp_path)
    conn = connect(db)
    try:
        # No such canonical event: skipped rather than violating the FK.
        assert apply_link_decisions(conn, [LinkDecision("rw-1", "usgs:ghost")]) == 0
        assert reliefweb_links(conn) == {}
    finally:
        conn.close()


# --- decisions file round-trip + determinism ----------------------------------


def test_decisions_file_roundtrips_and_is_sorted(tmp_path):
    path = tmp_path / "link_decisions.json"
    decisions = [LinkDecision("rw-2", "usgs:b"), LinkDecision("rw-1", "usgs:a")]
    write_decisions(path, decisions)
    loaded = load_decisions(path)
    assert {(d.reliefweb_id, d.canonical_id) for d in loaded} == {
        ("rw-2", "usgs:b"), ("rw-1", "usgs:a"),
    }
    # Sorted by reliefweb_id -> stable, reviewable diff.
    doc = json.loads(path.read_text())
    assert [d["reliefweb_id"] for d in doc["decisions"]] == ["rw-1", "rw-2"]
    # Byte-deterministic regardless of input order.
    other = tmp_path / "other.json"
    write_decisions(other, list(reversed(decisions)))
    assert path.read_text() == other.read_text()


def test_load_missing_file_is_empty(tmp_path):
    assert load_decisions(tmp_path / "nope.json") == []


# --- the fast tick applies decisions from the disjoint file (end to end) ------


def test_tick_applies_decisions_and_override_changes_merge(tmp_path):
    """The fast tick (sole ledger writer) reads the disjoint decisions file and
    applies it; a later file with a different mapping changes the merge next run."""
    db = tmp_path / "ledger.db"
    out = tmp_path / "dash.html"
    dfile = tmp_path / "link_decisions.json"
    usgs = FixtureFeedSource(_FIX / "usgs" / "slice5_run1.json")
    gdacs = FixtureFeedSource(_FIX / "gdacs" / "slice5_run1.json")

    # Tick 1 seeds the ledger; no decisions file yet -> no ReliefWeb links.
    run(usgs, _CLOCK, db, out, gdacs_source=gdacs, link_decisions_path=dfile)
    assert reliefweb_links(connect(db)) == {}

    # Record a decision, then tick again -> the tick applies it.
    write_decisions(dfile, [LinkDecision("rw-100", "usgs:us7000ong")])
    run(FixtureFeedSource(_FIX / "usgs" / "slice5_run1.json"), _CLOCK, db, out,
        gdacs_source=FixtureFeedSource(_FIX / "gdacs" / "slice5_run1.json"),
        link_decisions_path=dfile)
    assert reliefweb_links(connect(db)) == {"rw-100": "usgs:us7000ong"}

    # Override the file; the next tick re-links to the new event.
    write_decisions(dfile, [LinkDecision("rw-100", "usgs:us7000corr", method="override")])
    run(FixtureFeedSource(_FIX / "usgs" / "slice5_run1.json"), _CLOCK, db, out,
        gdacs_source=FixtureFeedSource(_FIX / "gdacs" / "slice5_run1.json"),
        link_decisions_path=dfile)
    assert reliefweb_links(connect(db)) == {"rw-100": "usgs:us7000corr"}
