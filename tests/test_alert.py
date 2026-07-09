"""Slice-4 headline: a severe, high-confidence quake breaks silence with ONE
push — and both the fire/no-fire decision and the message are produced by the
deterministic core, NO LLM on this path.

Two layers, both deterministic:
  * unit — the pure ``decide_alert`` / ``compose_message`` in isolation;
  * pipeline — driven end-to-end at the feed-fetch seam with a frozen clock and a
    ``RecordingPushSink``, asserting the decision as data (which alerts fired, at
    what level, with what message) plus one-push-per-event across stateless ticks.
"""

import sqlite3

from hadr.alert import Alert, EventFacts, compose_message, decide_alert
from hadr.pipeline import run
from hadr.push import RecordingPushSink

# Canonical ids used by the Slice-4 fixtures.
_PADANG = "usgs:us7000abcd"       # USGS+GDACS combined (NEIC crosswalk)
_SUMATRA = "gdacs:EQ:1570000"     # GDACS-only Red event
_TESTVILLE = "usgs:us8000test"    # PAGER orange->red escalation
_AS_OF = "2026-07-08 08:30 SGT"   # the frozen clock (00:30 UTC) in SGT


def _facts(**kw) -> EventFacts:
    base = dict(
        canonical_id="usgs:x", status="confirmed",
        gdacs_episodealertlevel=None, pager_alert=None, last_pushed_level=None,
        title="t", magnitude=6.8, place="somewhere",
    )
    base.update(kw)
    return EventFacts(**base)


def _last_pushed(db_path, canonical_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT last_pushed_level FROM canonical_events WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        return row["last_pushed_level"] if row is not None else None
    finally:
        conn.close()


def _status(db_path, canonical_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status FROM canonical_events WHERE canonical_id = ?",
            (canonical_id,),
        ).fetchone()
        return row["status"] if row is not None else None
    finally:
        conn.close()


# --- unit: the pure decision --------------------------------------------------

def test_severe_confirmed_fires():
    alert = decide_alert(_facts(gdacs_episodealertlevel="Red"), _AS_OF)
    assert alert is not None
    assert alert.level == "red"


def test_severe_but_provisional_does_not_fire():
    # A pre-ShakeMap GDACS Red is severe but not yet confirmed -> silent.
    assert decide_alert(
        _facts(status="provisional", gdacs_episodealertlevel="Red"), _AS_OF
    ) is None


def test_strong_magnitude_alone_never_fires():
    # No impact signal, however large the quake -> no escape hatch.
    assert decide_alert(
        _facts(magnitude=9.0, gdacs_episodealertlevel="Green", pager_alert=None),
        _AS_OF,
    ) is None


def test_gdacs_orange_is_not_severe():
    # Per the PRD the GDACS urgent trigger is Red only.
    assert decide_alert(_facts(gdacs_episodealertlevel="Orange"), _AS_OF) is None


def test_no_refire_at_same_level():
    assert decide_alert(
        _facts(pager_alert="orange", last_pushed_level="orange"), _AS_OF
    ) is None


def test_escalation_refires():
    # Already pushed Orange; now Red is strictly worse -> fire again at Red.
    alert = decide_alert(
        _facts(pager_alert="red", last_pushed_level="orange"), _AS_OF
    )
    assert alert is not None and alert.level == "red"


def test_downgrade_does_not_fire():
    # Was Red (pushed red), now Green -> no longer severe -> silent.
    assert decide_alert(
        _facts(gdacs_episodealertlevel="Green", last_pushed_level="red"), _AS_OF
    ) is None


def test_message_is_deterministic_function_of_facts():
    facts = _facts(
        gdacs_episodealertlevel="Red", pager_alert="orange",
        magnitude=6.8, place="120 km SW of Padang, Indonesia",
    )
    expected = (
        "URGENT: M6.8 earthquake — 120 km SW of Padang, Indonesia. "
        "GDACS Red; PAGER Orange. Confirmed, as of 2026-07-08 08:30 SGT."
    )
    assert compose_message(facts, _AS_OF) == expected
    # Pure: same inputs -> same string, and decide_alert carries the same message.
    assert compose_message(facts, _AS_OF) == expected
    assert decide_alert(facts, _AS_OF).message == expected


def test_message_omits_missing_signals_cleanly():
    # GDACS-only Red: no PAGER clause, no null-place em-dash noise.
    facts = _facts(gdacs_episodealertlevel="Red", pager_alert=None, place=None,
                   magnitude=None)
    msg = compose_message(facts, _AS_OF)
    assert "PAGER" not in msg
    assert msg == "URGENT: Earthquake. GDACS Red. Confirmed, as of 2026-07-08 08:30 SGT."


# --- pipeline: fires once + exact message -------------------------------------

def test_fires_once_with_deterministic_message(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    sink = RecordingPushSink()
    result = run(
        fixture_source("urgent_padang.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_padang_red.json"),
        push_sink=sink,
    )

    # Exactly one push, delivered once, surfaced in RunResult as data.
    assert len(sink.sent) == 1
    assert len(result.alerts_pushed) == 1
    alert = result.alerts_pushed[0]
    assert isinstance(alert, Alert)
    assert alert.canonical_id == _PADANG
    assert alert.level == "red"
    assert alert.message == (
        "URGENT: M6.8 earthquake — 120 km SW of Padang, Indonesia. "
        "GDACS Red; PAGER Orange. Confirmed, as of 2026-07-08 08:30 SGT."
    )
    # The push level is persisted for one-push-per-event.
    assert _last_pushed(tmp_ledger, _PADANG) == "red"


def test_no_refire_on_stateless_rerun(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # PAGER Orange lands -> confirmed + severe -> fires once.
    run(fixture_source("lifecycle_provisional.json"), frozen_clock, tmp_ledger, tmp_out,
        push_sink=RecordingPushSink())
    first = RecordingPushSink()
    run(fixture_source("lifecycle_pager.json"), frozen_clock, tmp_ledger, tmp_out,
        push_sink=first)
    assert [a.level for a in first.sent] == ["orange"]

    # Re-run the SAME severe tick, fresh sink + fresh connection -> no re-fire.
    second = RecordingPushSink()
    run(fixture_source("lifecycle_pager.json"), frozen_clock, tmp_ledger, tmp_out,
        push_sink=second)
    assert second.sent == []
    assert _last_pushed(tmp_ledger, _TESTVILLE) == "orange"


def test_escalation_orange_to_red_refires(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    sink = RecordingPushSink()
    # PAGER Orange -> push orange.
    run(fixture_source("lifecycle_pager.json"), frozen_clock, tmp_ledger, tmp_out,
        push_sink=sink)
    # A later tick reaches PAGER Red -> a second push at Red.
    run(fixture_source("urgent_pager_red.json"), frozen_clock, tmp_ledger, tmp_out,
        push_sink=sink)

    assert [a.level for a in sink.sent] == ["orange", "red"]
    assert _last_pushed(tmp_ledger, _TESTVILLE) == "red"


def test_gdacs_orange_then_red_fires_once_at_red(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # DoD point 1 (GDACS variant): a settled GDACS Orange is confirmed but NOT
    # severe, so it stays silent; the escalation to Red fires exactly once.
    sink = RecordingPushSink()
    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("eq_padang.json"), push_sink=sink)
    assert sink.sent == []                       # Orange (confirmed) -> no push
    assert _status(tmp_ledger, _PADANG) == "confirmed"

    run(fixture_source("all_day.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_padang_red.json"), push_sink=sink)
    assert [a.level for a in sink.sent] == ["red"]   # escalation to Red fires once
    assert _last_pushed(tmp_ledger, _PADANG) == "red"


# --- pipeline: the no-push cases ----------------------------------------------

def test_pre_shakemap_gdacs_red_is_provisional_no_push(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    sink = RecordingPushSink()
    run(fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_gdacs_red_temp.json"), push_sink=sink)

    assert _status(tmp_ledger, _SUMATRA) == "provisional"  # istemporary true
    assert sink.sent == []                                 # severe but unconfirmed
    assert _last_pushed(tmp_ledger, _SUMATRA) is None


def test_strong_provisional_quake_no_push(
    fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # M7.2, automatic, PAGER null, no GDACS -> not severe at all -> no push.
    sink = RecordingPushSink()
    result = run(fixture_source("urgent_strong_provisional.json"), frozen_clock,
                 tmp_ledger, tmp_out, push_sink=sink)
    assert sink.sent == []
    assert result.alerts_pushed == ()


def test_downgrade_to_green_no_new_push(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # Confirmed GDACS Red -> pushes once.
    first = RecordingPushSink()
    run(fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_gdacs_red_settled.json"),
        push_sink=first)
    assert [a.level for a in first.sent] == ["red"]

    # Episode drops to Green -> no longer severe -> no NEW push (already-pushed
    # event stays silent on a downgrade).
    second = RecordingPushSink()
    run(fixture_source("all_day_below_floor.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_gdacs_green_settled.json"),
        push_sink=second)
    assert second.sent == []
    assert _last_pushed(tmp_ledger, _SUMATRA) == "red"   # unchanged


def test_push_sink_none_does_not_evaluate_or_push(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out
):
    # The brief context: no sink -> the decision is not evaluated, nothing persisted.
    result = run(
        fixture_source("urgent_padang.json"), frozen_clock, tmp_ledger, tmp_out,
        gdacs_source=gdacs_fixture_source("urgent_padang_red.json"),
    )
    assert result.alerts_pushed == ()
    assert _last_pushed(tmp_ledger, _PADANG) is None


def test_push_decision_is_reproducible_across_fresh_runs(
    fixture_source, gdacs_fixture_source, frozen_clock, tmp_ledger, tmp_out, tmp_path
):
    # Same fixture + same frozen clock -> byte-identical message, in a fresh DB.
    def _msg(db):
        sink = RecordingPushSink()
        run(fixture_source("urgent_padang.json"), frozen_clock, db,
            tmp_path / "out.html",
            gdacs_source=gdacs_fixture_source("urgent_padang_red.json"),
            push_sink=sink)
        return sink.sent[0].message

    assert _msg(tmp_path / "a.db") == _msg(tmp_path / "b.db")
