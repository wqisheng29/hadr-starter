"""Slice-5 headline: the self-correcting 08:30 brief, driven at the feed-fetch
seam with a frozen clock (house style). A two-run fixture sequence exercises, in
ONE run-2 brief, every "since the last brief" bucket — New / Upgraded /
Downgraded / Retracted / Aged-out / Correction — plus the invariants: the brief
is READ-ONLY on the ledger, "nothing changed" publishes a deterministic "no
material change" state, and the diff is byte-deterministic.

The tick (``pipeline.run``) builds the ledger; the brief (``brief.write_brief``)
only reads it and the last published snapshot, and writes disjoint artifacts.
"""

import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hadr.brief import write_brief
from hadr.clock import FrozenClock
from hadr.fetch import RELIEFWEB_SOURCE, USGS_SOURCE, FixtureFeedSource
from hadr.ledger import connect, reconcile_absences
from hadr.model import FetchOutcome
from hadr.pipeline import run

_FIX = Path(__file__).resolve().parents[1] / "fixtures"

_DAY1 = FrozenClock(datetime(2026, 7, 7, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-07
_DAY2 = FrozenClock(datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-08
_DAY3 = FrozenClock(datetime(2026, 7, 9, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-09


def _usgs(name):
    return FixtureFeedSource(_FIX / "usgs" / name)


def _gdacs(name):
    return FixtureFeedSource(_FIX / "gdacs" / name)


@pytest.fixture
def env(tmp_path):
    """A ledger + published dir + out paths for a two-run brief scenario."""
    return {
        "db": tmp_path / "ledger.db",
        "pub": tmp_path / "published",
        "tick": tmp_path / "tick.html",
        "brief": tmp_path / "brief.html",
    }


def _seed_run1(env):
    run(_usgs("slice5_run1.json"), _DAY1, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run1.json"))
    return write_brief(env["db"], env["brief"], env["pub"], _DAY1)


def _brief_run2(env):
    run(_usgs("slice5_run2.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run2.json"))
    return write_brief(env["db"], env["brief"], env["pub"], _DAY2)


# --- run 1 publishes a readable snapshot --------------------------------------

def test_run1_publishes_readable_snapshot(env):
    result = _seed_run1(env)

    snap = env["pub"] / "2026-07-07.json"
    assert snap.exists()
    assert Path(result.snapshot_path) == snap

    doc = json.loads(snap.read_text())
    assert doc["schema_version"] == 1
    assert doc["as_of"]["sgt"] == "2026-07-07 08:30 SGT"
    ids = {e["canonical_id"] for e in doc["events"]}
    # The four seeded USGS events + the GDACS-only downgrade event.
    assert ids == {
        "usgs:us7000corr", "usgs:us7000retr", "usgs:us7000aged",
        "usgs:us7000ong", "gdacs:EQ:1560500",
    }
    # A SnapshotEvent carries the impact tier (Red -> 2 for the GDACS event).
    gdacs_ev = next(e for e in doc["events"] if e["canonical_id"] == "gdacs:EQ:1560500")
    assert gdacs_ev["tier"] == 2


# --- run 2 buckets every change correctly -------------------------------------

def test_run2_diff_buckets_each_change(env):
    _seed_run1(env)
    result = _brief_run2(env)

    assert result.no_material_change is False
    assert result.counts == {
        "New": 1, "Upgraded": 0, "Downgraded": 1, "Retracted": 1,
        "Aged-out": 1, "Correction": 1, "Ongoing": 1,
    }


def test_run2_dashboard_top_section_shows_buckets_and_correction(env):
    _seed_run1(env)
    _brief_run2(env)
    html = env["brief"].read_text()

    assert "Since the last brief" in html
    # Each change bucket heading present.
    for heading in ("New (1)", "Downgraded (1)", "Retracted (1)",
                    "Aged-out (1)", "Correction (1)"):
        assert heading in html

    # The right event lands in the right bucket. Split at each heading and check
    # the event title falls under it (before the next bucket).
    def _section(after: str) -> str:
        return html.split(after, 1)[1]

    assert "70 km SE of Antofagasta, Chile" in _section("New (1)").split("<h3")[0]
    assert "West Sumatra" in _section("Downgraded (1)").split("<h3")[0]
    assert "Reykjavik" in _section("Retracted (1)").split("<h3")[0]
    assert "Tokyo" in _section("Aged-out (1)").split("<h3")[0]

    # Correction phrasing: attributed to the source's revision, not the monitor's
    # error (M6.8 -> M6.1). The '>' is HTML-escaped by autoescape.
    assert "we said M6.8" in html
    assert "it is now M6.1" in html
    assert "the source revised it" in html

    # Downgrade wording is a reassessment, NOT a retraction.
    assert "we said GDACS Red" in html
    assert "the source reassessed it" in html


def test_downgrade_is_not_retracted_and_retraction_is_not_downgrade(env):
    """The load-bearing distinction, end to end: the colour-drop event is bucketed
    Downgraded (still real), the source-deleted event is Retracted."""
    _seed_run1(env)
    _brief_run2(env)
    conn = connect(env["db"])
    try:
        def _status(cid):
            return conn.execute(
                "SELECT status FROM canonical_events WHERE canonical_id = ?", (cid,)
            ).fetchone()["status"]
        assert _status("gdacs:EQ:1560500") == "confirmed"   # colour drop -> still real
        assert _status("usgs:us7000retr") == "retracted"    # positive withdrawal
        assert _status("usgs:us7000aged") == "aged_out"     # left the window
    finally:
        conn.close()


# --- "nothing changed" -> deterministic no-material-change, no push -----------

def test_no_material_change_rebrief(env):
    _seed_run1(env)
    _brief_run2(env)

    # A third brief on a later day with the ledger unchanged: every event folds to
    # Ongoing -> a deterministic "no material change" state. (The brief never
    # pushes — there is no push path in write_brief at all.)
    result = write_brief(env["db"], env["brief"], env["pub"], _DAY3)
    assert result.no_material_change is True
    assert all(result.counts[b] == 0 for b in result.counts if b != "Ongoing")
    assert result.counts["Ongoing"] == 6

    html = env["brief"].read_text()
    assert "No material change since the last brief." in html


# --- single-writer: the brief NEVER writes the ledger -------------------------

def _fingerprint(db):
    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT canonical_id, status, magnitude, last_updated "
            "FROM canonical_events ORDER BY canonical_id"
        ).fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def test_brief_never_writes_the_ledger(env):
    _seed_run1(env)
    run(_usgs("slice5_run2.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run2.json"))

    before = _fingerprint(env["db"])
    write_brief(env["db"], env["brief"], env["pub"], _DAY2)
    after = _fingerprint(env["db"])

    # Every row's status/magnitude/last_updated is untouched by the brief.
    assert before == after


def test_brief_ledger_connection_is_read_only(env):
    _seed_run1(env)
    from hadr.brief import _connect_readonly

    conn = _connect_readonly(env["db"])
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE canonical_events SET status = 'x'")
            conn.commit()
    finally:
        conn.close()


# --- determinism: byte-identical dashboard + snapshot across re-briefs ---------

def test_brief_is_byte_deterministic(env, tmp_path):
    _seed_run1(env)
    run(_usgs("slice5_run2.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run2.json"))

    out_a = tmp_path / "a.html"
    out_b = tmp_path / "b.html"
    write_brief(env["db"], out_a, env["pub"], _DAY2)
    snap_a = (env["pub"] / "2026-07-08.json").read_text()
    write_brief(env["db"], out_b, env["pub"], _DAY2)
    snap_b = (env["pub"] / "2026-07-08.json").read_text()

    assert out_a.read_text() == out_b.read_text()
    assert snap_a == snap_b


# --- absence detection: retraction vs aged-out vs outage (at the seam) ---------

def test_in_window_disappearance_is_retracted(env):
    # A GDACS event vanishes from a REACHABLE feed while still in window -> a
    # positive within-window withdrawal -> retracted (not aged_out).
    run(_usgs("all_day_below_floor.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run1.json"))
    conn = connect(env["db"])
    assert conn.execute(
        "SELECT status FROM canonical_events WHERE canonical_id = 'gdacs:EQ:1560500'"
    ).fetchone()["status"] == "confirmed"
    conn.close()

    run(_usgs("all_day_below_floor.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("empty.json"))
    conn = connect(env["db"])
    assert conn.execute(
        "SELECT status FROM canonical_events WHERE canonical_id = 'gdacs:EQ:1560500'"
    ).fetchone()["status"] == "retracted"
    conn.close()


def test_unreachable_feed_does_not_mark_absence(env):
    # An outage must never read as a retraction/aged-out (degradation).
    run(_usgs("all_day_below_floor.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=_gdacs("slice5_run1.json"))

    class Failing:
        def fetch(self):
            return FetchOutcome(ok=False, status=503)

    run(_usgs("all_day_below_floor.json"), _DAY2, env["db"], env["tick"],
        gdacs_source=Failing())

    conn = connect(env["db"])
    assert conn.execute(
        "SELECT status FROM canonical_events WHERE canonical_id = 'gdacs:EQ:1560500'"
    ).fetchone()["status"] == "confirmed"   # untouched, not retracted
    conn.close()


def test_reliefweb_disappearance_is_never_a_retraction(env):
    # PRD Q5 / Slice-5 DoD: a vanished ReliefWeb RSS item is "aged-out unless
    # corroborated, never a retraction" — because the tick never re-polls
    # ReliefWeb, its absence can imply no withdrawal. Only USGS/GDACS (the polled
    # feeds) can. Here an event vouched ONLY by ReliefWeb sits WITHIN the feed
    # windows (fresh origin), so if ReliefWeb were treated as pollable it would be
    # RETRACTED; the source type, not the age, is what must spare it. Built at the
    # ledger seam because the pipeline never mints a ReliefWeb-only event.
    now = _DAY2.now()
    conn = connect(env["db"])
    try:
        conn.execute(
            "INSERT INTO canonical_events "
            "(canonical_id, status, first_seen, last_updated, origin_time) "
            "VALUES (?, ?, ?, ?, ?)",
            ("reliefweb:rw-only", "confirmed", now.isoformat(), now.isoformat(),
             now.isoformat()),   # within-window: age is not the reason it survives
        )
        conn.execute(
            "INSERT INTO feed_identifiers (source, feed_id, canonical_id) "
            "VALUES (?, ?, ?)",
            (RELIEFWEB_SOURCE, "rw-1", "reliefweb:rw-only"),
        )
        conn.commit()

        # The item is gone this run (not in ``seen``) and USGS was reachable, yet
        # the sole vouching feed (ReliefWeb) is not pollable -> no state change.
        changed = reconcile_absences(
            conn, seen=set(), reachable_sources={USGS_SOURCE}, clock=_DAY2)

        assert changed == 0
        assert conn.execute(
            "SELECT status FROM canonical_events WHERE canonical_id = 'reliefweb:rw-only'"
        ).fetchone()["status"] == "confirmed"   # never retracted, never aged-out
    finally:
        conn.close()


# --- CLI wiring (mirrors tests/test_run_cli.py) -------------------------------

def _load_brief_cli():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("brief_cli", root / "scripts" / "brief.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_happy_path_reads_ledger_writes_artifacts(env, capsys):
    _seed_run1(env)
    cli = _load_brief_cli()
    rc = cli.main([
        "--db", str(env["db"]), "--out", str(env["brief"]),
        "--published-dir", str(env["pub"]), "--as-of", "2026-07-07T00:30:00Z",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    # First brief: everything is New (no prior snapshot < today); 5 seeded events.
    assert "New=5" in out
    assert env["brief"].read_text()  # dashboard produced


def test_cli_bad_as_of_exits_two(env, capsys):
    cli = _load_brief_cli()
    rc = cli.main([
        "--db", str(env["db"]), "--out", str(env["brief"]),
        "--published-dir", str(env["pub"]), "--as-of", "not-a-timestamp",
    ])
    assert rc == 2
    assert "bad --as-of" in capsys.readouterr().err
