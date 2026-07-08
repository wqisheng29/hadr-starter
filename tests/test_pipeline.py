"""End-to-end DoD scenarios, driven at the feed-fetch seam."""

import sqlite3

from hadr.ledger import connect, read_events
from hadr.model import FeedState
from hadr.pipeline import run


def _rows(db_path):
    conn = connect(db_path)
    try:
        return read_events(conn)
    finally:
        conn.close()


def test_qualifying_persisted(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    result = run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)

    # 3 of 5 features clear the M4.5 floor (6.8, 5.1, 4.5); M3.0 and null are dropped.
    assert result.rows_written == 3
    events = _rows(tmp_ledger)
    assert len(events) == 3

    top = events[0]  # ordered magnitude desc
    assert top.magnitude == 6.8
    assert top.title == "M 6.8 - 120 km SW of Padang, Indonesia"
    assert top.place == "120 km SW of Padang, Indonesia"
    assert top.origin_time is not None


def test_below_floor_dropped(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    result = run(fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out)
    assert result.rows_written == 0
    assert _rows(tmp_ledger) == []
    # M4.49 must NOT sneak past a >= 4.5 floor.
    assert "No earthquakes" in tmp_out.read_text()


def test_idempotent_rerun(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)
    first_html = tmp_out.read_text()

    result = run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)

    assert result.rows_written == 0  # nothing changed -> no writes
    assert len(_rows(tmp_ledger)) == 3
    conn = sqlite3.connect(tmp_ledger)
    try:
        assert conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM feed_identifiers").fetchone()[0] == 5
    finally:
        conn.close()
    assert tmp_out.read_text() == first_html  # byte-identical


def test_id_change_keeps_one_canonical(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)
    run(fixture_source("all_day_revised.json"), frozen_clock, tmp_ledger, tmp_out)

    conn = sqlite3.connect(tmp_ledger)
    conn.row_factory = sqlite3.Row
    try:
        # Padang quake changed preferred id (us7000abcd -> at00abcd) and gained
        # pt00abcd, but stays ONE canonical event via union-of-ids.
        row = conn.execute(
            "SELECT canonical_id, usgs_ids FROM canonical_events "
            "WHERE canonical_id = 'usgs:us7000abcd'"
        ).fetchone()
        assert row is not None
        assert set(__import__("json").loads(row["usgs_ids"])) == {
            "us7000abcd", "at00abcd", "pt00abcd"
        }
        # Still 3 canonical events total (no phantom duplicate).
        assert conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0] == 3
    finally:
        conn.close()

    events = _rows(tmp_ledger)
    assert len(events) == 3


def test_non_200_degrades_without_crash(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    # Seed the ledger, then hit an unreachable feed: last picture must survive.
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)

    class Failing:
        def fetch(self):
            from hadr.model import FetchOutcome
            return FetchOutcome(ok=False, status=503)

    result = run(Failing(), frozen_clock, tmp_ledger, tmp_out)

    assert result.feed_status.state is FeedState.UNREACHABLE
    assert result.rows_written == 0
    assert len(_rows(tmp_ledger)) == 3  # unchanged, not wiped
    html = tmp_out.read_text()
    assert "unreachable" in html
    assert "HTTP 503" in html


def test_unparseable_degrades_without_crash(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    result = run(fixture_source("malformed.json"), frozen_clock, tmp_ledger, tmp_out)
    assert result.feed_status.state is FeedState.UNPARSEABLE
    assert result.rows_written == 0
    assert _rows(tmp_ledger) == []
    assert "unparseable" in tmp_out.read_text()


def test_dashboard_structure_and_sgt_header(fixture_source, frozen_clock, tmp_ledger, tmp_out):
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)
    html = tmp_out.read_text()

    # 08:30 SGT header (00:30 UTC + 8h).
    assert "as of 2026-07-08 08:30 SGT" in html
    # Each qualifying quake's fields present.
    assert "M 6.8 - 120 km SW of Padang, Indonesia" in html
    assert "M6.8" in html
    assert "42 km E of Hualien, Taiwan" in html
