"""Slice-3 headline: the provisional -> confirmed lifecycle.

Every canonical event is recorded PROVISIONAL on first detection and firms to
CONFIRMED when a feed-native impact signal settles across a fixture's ticks —
USGS automatic->reviewed, a non-null PAGER colour, or GDACS istemporary flipping
off. Driven end-to-end at the feed-fetch seam with a frozen clock, like the
earlier slices. NO enrichment fetch: confirmation reflects only signals already
in the feed records.
"""

from datetime import datetime, timezone

from hadr.clock import FrozenClock
from hadr.ledger import connect, read_events, reconcile
from hadr.model import QuakeRecord
from hadr.pipeline import run

# Canonical ids used by the fixtures.
_PADANG = "usgs:us7000abcd"           # M6.8, automatic + null PAGER in all_day.json
_AVALON = "usgs:ci41287863"           # M4.5, automatic + null PAGER (a routine quake)
_TESTVILLE = "usgs:us8000test"        # the isolated single-quake lifecycle fixtures
_GDACS_BANDA = "gdacs:EQ:1560000"     # GDACS-only quake in the lifecycle_* fixtures


def _events(db_path):
    conn = connect(db_path)
    try:
        return read_events(conn)
    finally:
        conn.close()


def _status(db_path, canonical_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM canonical_events WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        return row["status"] if row is not None else None
    finally:
        conn.close()


# --- provisional on first detection -------------------------------------------

def test_new_automatic_null_pager_is_provisional(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)

    # A fresh automatic detection with a null PAGER has no confirming signal.
    assert _status(tmp_ledger, _PADANG) == "provisional"
    assert _status(tmp_ledger, _AVALON) == "provisional"
    # Hualien ships as status=reviewed in the same fixture -> already confirmed.
    assert _status(tmp_ledger, "usgs:us7000efgh") == "confirmed"

    # A provisional read is never presented as settled: at least one row carries
    # the rendered provisional tag. (Assert the row markup, not the bare word —
    # the CSS block always mentions both tag classes.)
    assert 'class="tag provisional"' in tmp_out.read_text()


# --- USGS automatic -> reviewed promotes --------------------------------------

def test_usgs_automatic_to_reviewed_promotes(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    run(fixture_source("lifecycle_provisional.json"), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, _TESTVILLE) == "provisional"
    # Single event, provisional: its rendered row tag is provisional, not confirmed.
    html = tmp_out.read_text()
    assert 'class="tag provisional"' in html
    assert 'class="tag confirmed"' not in html

    run(fixture_source("lifecycle_reviewed.json"), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, _TESTVILLE) == "confirmed"
    # The very same event now renders the confirmed tag, and no provisional one.
    html = tmp_out.read_text()
    assert 'class="tag confirmed"' in html
    assert 'class="tag provisional"' not in html


# --- USGS PAGER null -> non-null promotes -------------------------------------

def test_usgs_pager_null_to_nonnull_promotes(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    run(fixture_source("lifecycle_provisional.json"), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, _TESTVILLE) == "provisional"

    # Still status=automatic, but PAGER has now run (alert "orange").
    run(fixture_source("lifecycle_pager.json"), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, _TESTVILLE) == "confirmed"


# --- GDACS istemporary true -> false promotes ---------------------------------

def test_gdacs_istemporary_true_to_false_promotes(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # USGS feed carries nothing above the floor; GDACS drives this event alone.
    run(
        fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("lifecycle_temp.json"),
    )
    assert _status(tmp_ledger, _GDACS_BANDA) == "provisional"

    # A settled ShakeMap flips istemporary off -> confirmed.
    run(
        fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("lifecycle_settled.json"),
    )
    assert _status(tmp_ledger, _GDACS_BANDA) == "confirmed"


# --- confirmation is sticky ---------------------------------------------------

def test_confirmation_is_sticky_across_ticks(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # Settle first (istemporary false -> confirmed) ...
    run(
        fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("lifecycle_settled.json"),
    )
    assert _status(tmp_ledger, _GDACS_BANDA) == "confirmed"

    # ... then a later tick that lacks the signal (istemporary true) must NOT demote.
    run(
        fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("lifecycle_temp.json"),
    )
    assert _status(tmp_ledger, _GDACS_BANDA) == "confirmed"


def test_sticky_at_ledger_level_via_usgs(tmp_ledger):
    """Unit-level stickiness: a reviewed quake that reverts to automatic on a
    later tick stays confirmed (and never regresses)."""
    clock = FrozenClock(datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc))

    def _rec(status):
        return QuakeRecord(
            preferred_id="us1", ids=frozenset({"us1"}), magnitude=5.0,
            place="p", title="t", origin_time_ms=1783300000000,
            updated_ms=1783300000000, longitude=1.0, latitude=2.0, depth_km=10.0,
            status=status, pager_alert=None,
        )

    conn = connect(tmp_ledger)
    try:
        reconcile(conn, [_rec("reviewed")], clock)
        assert conn.execute("SELECT status FROM canonical_events").fetchone()[0] == "confirmed"
        reconcile(conn, [_rec("automatic")], clock)
        assert conn.execute("SELECT status FROM canonical_events").fetchone()[0] == "confirmed"
    finally:
        conn.close()


# --- determinism + idempotency of the new columns -----------------------------

def test_status_columns_do_not_churn_on_rerun(tmp_ledger):
    """A plain re-run with a later clock bumps nothing — including the lifecycle
    status and signal columns."""
    early = FrozenClock(datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc))
    late = FrozenClock(datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc))

    def _rec():
        return QuakeRecord(
            preferred_id="us1", ids=frozenset({"us1"}), magnitude=5.0,
            place="p", title="t", origin_time_ms=1783300000000,
            updated_ms=1783300000000, longitude=1.0, latitude=2.0, depth_km=10.0,
            status="reviewed", pager_alert=None,
        )

    conn = connect(tmp_ledger)
    try:
        assert reconcile(conn, [_rec()], early) == 1
        first = conn.execute(
            "SELECT status, last_updated FROM canonical_events"
        ).fetchone()
        assert first["status"] == "confirmed"

        # Identical data, later clock -> no write, last_updated frozen.
        assert reconcile(conn, [_rec()], late) == 0
        after = conn.execute(
            "SELECT status, last_updated FROM canonical_events"
        ).fetchone()
        assert after["status"] == "confirmed"
        assert after["last_updated"] == first["last_updated"]
    finally:
        conn.close()


def test_combined_promotion_deterministic_and_idempotent(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    def _run():
        return run(
            fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
            gdacs_source=gdacs_fixture_source("eq_padang.json"),
        )

    _run()
    first_html = tmp_out.read_text()
    # Padang: GDACS istemporary false + Orange + USGS reviewed-in-revised? no; in
    # all_day.json Padang is automatic/null, but GDACS eq_padang is istemporary
    # false -> confirmed via the GDACS settle.
    assert _status(tmp_ledger, _PADANG) == "confirmed"

    result = _run()
    assert result.rows_written == 0            # combined re-run writes nothing
    assert tmp_out.read_text() == first_html    # byte-identical re-render
    assert _status(tmp_ledger, _PADANG) == "confirmed"


# --- dashboard classification: headline vs below-the-fold ---------------------

def test_dashboard_headlines_material_folds_routine(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    run(
        fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("eq_padang.json"),
    )
    html = tmp_out.read_text()

    # The routine section exists and holds the Green ~M4.5 provisional Avalon
    # swarm; the confirmed/material quakes headline above it.
    assert 'class="routine"' in html
    headline_part, routine_part = html.split('class="routine"', 1)

    # Avalon (M4.5, automatic, null PAGER, no GDACS) is folded below the fold.
    assert "9 km NNE of Avalon, CA" in routine_part
    assert "9 km NNE of Avalon, CA" not in headline_part

    # Padang (confirmed + Orange) is headlined, not folded.
    assert "120 km SW of Padang, Indonesia" in headline_part
    assert "120 km SW of Padang, Indonesia" not in routine_part


def test_strong_provisional_quake_headlines_on_magnitude(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # USGS-only, no GDACS/PAGER: Padang M6.8 is automatic + null-PAGER, hence
    # provisional — but a major quake must NOT be buried below the fold just
    # because impact scoring hasn't landed yet. It headlines (tagged provisional),
    # while the M4.5 Avalon quake still folds into routine.
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out)
    html = tmp_out.read_text()

    assert _status(tmp_ledger, _PADANG) == "provisional"
    assert 'class="routine"' in html
    headline_part, routine_part = html.split('class="routine"', 1)
    assert "120 km SW of Padang, Indonesia" in headline_part   # strong -> headlined
    assert "9 km NNE of Avalon, CA" in routine_part            # minor -> folded
