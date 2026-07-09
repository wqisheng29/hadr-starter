"""Slice-2 headline: the same quake from USGS and GDACS is ONE canonical event.

Driven end-to-end at the feed-fetch seam, like the slice-1 pipeline tests."""

import sqlite3

from hadr.ledger import connect, read_events
from hadr.model import FeedState, FetchOutcome
from hadr.pipeline import run

_PADANG = "usgs:us7000abcd"
_PADANG_TITLE = "M 6.8 - 120 km SW of Padang, Indonesia"


def _events(db_path):
    conn = connect(db_path)
    try:
        return read_events(conn)
    finally:
        conn.close()


def test_usgs_and_gdacs_collapse_to_one_event(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    run(
        fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("eq_padang.json"),
    )

    events = _events(tmp_ledger)
    # Still 3 canonical events (Padang, Hualien, Avalon) — GDACS did NOT add a 4th.
    assert len(events) == 3

    padang = next(e for e in events if e.canonical_id == _PADANG)
    # Both feeds corroborate the one canonical event.
    assert padang.sources == ("gdacs", "usgs")
    # USGS-owned fields are NOT clobbered by GDACS; GDACS severity is recorded.
    assert padang.title == _PADANG_TITLE
    assert padang.magnitude == 6.8
    assert padang.gdacs_episodealertlevel == "Orange"

    conn = sqlite3.connect(tmp_ledger)
    conn.row_factory = sqlite3.Row
    try:
        # feed_identifiers carries both feeds' references for the same canonical id.
        srcs = {
            r["source"]
            for r in conn.execute(
                "SELECT source FROM feed_identifiers WHERE canonical_id = ?", (_PADANG,)
            )
        }
        assert {"usgs", "gdacs"} <= srcs
        assert conn.execute(
            "SELECT COUNT(*) FROM feed_identifiers "
            "WHERE canonical_id = ? AND source = 'gdacs'", (_PADANG,)
        ).fetchone()[0] == 1
    finally:
        conn.close()

    # Rendered exactly once.
    assert tmp_out.read_text().count(_PADANG_TITLE) == 1


def test_gdacs_unreachable_degrades_but_usgs_still_renders(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    class Failing:
        def fetch(self):
            return FetchOutcome(ok=False, status=503)

    result = run(
        fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=Failing(),
    )

    # USGS succeeded; only GDACS degraded.
    assert result.feed_status.state is FeedState.OK
    gdacs_status = next(s for s in result.feed_statuses if s.source == "gdacs")
    assert gdacs_status.state is FeedState.UNREACHABLE
    assert any("GDACS feed unreachable" in w for w in result.warnings)

    # USGS events still present and rendered; GDACS banner noted.
    assert len(_events(tmp_ledger)) == 3
    html = tmp_out.read_text()
    assert _PADANG_TITLE in html
    assert "GDACS feed unreachable" in html


def test_combined_rerun_is_idempotent(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    def _run():
        return run(
            fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
            gdacs_source=gdacs_fixture_source("eq_padang.json"),
        )

    _run()
    first_html = tmp_out.read_text()
    result = _run()

    assert result.rows_written == 0            # nothing changed -> no writes
    assert len(_events(tmp_ledger)) == 3
    assert tmp_out.read_text() == first_html    # byte-identical re-render
