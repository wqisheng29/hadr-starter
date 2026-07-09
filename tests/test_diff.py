"""Slice-5 unit tests: the pure impact tier + the "since last brief" classifier.

These pin the distinctions the PRD (Q5) insists must never be conflated —
downgrade vs. retraction, and correction vs. re-rank — in isolation of the ledger
and the filesystem. ``hadr.diff`` is pure, so no clock/fixtures are needed here.
"""

from hadr import config
from hadr.diff import (
    AGED_OUT,
    CORRECTION,
    DOWNGRADED,
    NEW,
    ONGOING,
    RETRACTED,
    UPGRADED,
    SnapshotEvent,
    classify,
    impact_tier,
)


def _ev(canonical_id="usgs:x", magnitude=6.0, status=config.STATUS_CONFIRMED,
        gdacs=None, pager=None) -> SnapshotEvent:
    return SnapshotEvent(
        canonical_id=canonical_id, title="t", magnitude=magnitude, place="p",
        status=status, gdacs_episodealertlevel=gdacs, pager_alert=pager,
        origin_time="2026-07-08T00:00:00+00:00", sources=("usgs",),
        tier=impact_tier(status, gdacs, pager),
    )


# --- impact_tier: max severity across GDACS episode + PAGER, on one 0/1/2 scale -

def test_impact_tier_no_signal_is_zero():
    assert impact_tier(config.STATUS_PROVISIONAL, None, None) == 0


def test_impact_tier_gdacs_colours_rank():
    assert impact_tier(None, "Green", None) == 0
    assert impact_tier(None, "Orange", None) == 1
    assert impact_tier(None, "Red", None) == 2


def test_impact_tier_pager_yellow_and_green_share_low_tier():
    assert impact_tier(None, None, "green") == 0
    assert impact_tier(None, None, "yellow") == 0   # yellow/green below orange (PRD Q5)
    assert impact_tier(None, None, "orange") == 1
    assert impact_tier(None, None, "red") == 2


def test_impact_tier_takes_max_across_feeds():
    assert impact_tier(None, "Green", "red") == 2
    assert impact_tier(None, "Red", "green") == 2


def test_impact_tier_ignores_confirmation():
    # Confirmation is scored in classify(), never the tier ordinal.
    assert impact_tier(config.STATUS_PROVISIONAL, "Red", None) == impact_tier(
        config.STATUS_CONFIRMED, "Red", None
    )


# --- classify: new / ongoing --------------------------------------------------

def test_new_when_no_previous():
    assert classify(_ev(), None) == NEW


def test_ongoing_when_unchanged():
    prev = _ev(magnitude=5.0, gdacs="Orange")
    cur = _ev(magnitude=5.0, gdacs="Orange")
    assert classify(cur, prev) == ONGOING


# --- classify: downgrade is NEVER a retraction (the load-bearing distinction) --

def test_colour_drop_even_red_to_green_is_downgrade_not_retraction():
    prev = _ev(gdacs="Red")     # tier 2
    cur = _ev(gdacs="Green")    # tier 0 — a full drop, but the event is still real
    assert classify(cur, prev) == DOWNGRADED


def test_retraction_only_on_positive_withdrawal_status():
    prev = _ev(status=config.STATUS_CONFIRMED, gdacs="Red")
    cur = _ev(status=config.STATUS_RETRACTED, gdacs="Red")
    assert classify(cur, prev) == RETRACTED


def test_retraction_is_sticky_then_ongoing():
    prev = _ev(status=config.STATUS_RETRACTED)
    cur = _ev(status=config.STATUS_RETRACTED)
    assert classify(cur, prev) == ONGOING


def test_aged_out_distinct_from_retraction_and_sticky():
    prev = _ev(status=config.STATUS_CONFIRMED)
    assert classify(_ev(status=config.STATUS_AGED_OUT), prev) == AGED_OUT
    assert classify(_ev(status=config.STATUS_AGED_OUT),
                    _ev(status=config.STATUS_AGED_OUT)) == ONGOING


# --- classify: correction is NEVER a re-rank ----------------------------------

def test_magnitude_revision_without_tier_move_is_correction():
    prev = _ev(magnitude=6.8, gdacs=None)   # tier 0
    cur = _ev(magnitude=6.1, gdacs=None)    # tier 0 — same tier, mag revised
    assert classify(cur, prev) == CORRECTION


def test_tier_move_beats_magnitude_change_downgrade():
    # A magnitude revision that ALSO drops the tier is a Downgrade, not a bare
    # correction — the tier move takes precedence.
    prev = _ev(magnitude=6.8, gdacs="Red")
    cur = _ev(magnitude=6.1, gdacs="Green")
    assert classify(cur, prev) == DOWNGRADED


# --- classify: upgrade by tier or by confirmed-severe -------------------------

def test_upgrade_by_tier_increase():
    assert classify(_ev(gdacs="Red"), _ev(gdacs="Orange")) == UPGRADED


def test_upgrade_by_provisional_to_confirmed_at_material_tier():
    prev = _ev(status=config.STATUS_PROVISIONAL, gdacs="Orange")   # tier 1
    cur = _ev(status=config.STATUS_CONFIRMED, gdacs="Orange")      # tier 1, now confirmed-severe
    assert classify(cur, prev) == UPGRADED


def test_confirming_a_nonmaterial_event_is_not_an_upgrade():
    # provisional -> confirmed but tier stays below material: not an "Upgraded".
    prev = _ev(status=config.STATUS_PROVISIONAL, magnitude=4.8, gdacs="Green")
    cur = _ev(status=config.STATUS_CONFIRMED, magnitude=4.8, gdacs="Green")
    assert classify(cur, prev) == ONGOING
