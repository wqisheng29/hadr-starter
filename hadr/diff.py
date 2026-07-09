"""The "Since the last brief" diff — pure, deterministic, LLM-free (Slice 5).

The product's distinctive feature: the 08:30 brief leads with what changed since
the last published snapshot, classified by IMPACT TIER (GDACS episode colour +
PAGER), never by magnitude alone. This module is the whole classification, kept
pure so it is unit-testable in isolation of the ledger and the filesystem — the
brief (``hadr/brief.py``) just reads facts, calls ``build_diff``, and renders.

The distinctions the PRD (issue #3, Q5) insists must never be conflated:

* **Downgrade vs. retraction** — a colour drop (even Red->Green) is a Downgrade;
  the event is still real, just reassessed. Only a POSITIVE source withdrawal (a
  USGS deletion / a feed record removed) is a Retraction. The ledger encodes the
  withdrawal as ``status == retracted`` (set by ``ledger.reconcile*``); this
  module reads that status, it does not infer it from a colour.
* **Correction vs. re-rank** — a magnitude revision that does NOT move the impact
  tier is a Correction, not an Upgrade/Downgrade.
* **Aged-out vs. retraction** — an event that merely left a feed window carries
  ``status == aged_out``; it is "not confirmed ended", distinct from a retraction.
"""

from dataclasses import dataclass, field

from . import config
from .model import EventRow

# Bucket labels. These are the ordered sections the brief renders; the change
# buckets (everything but ONGOING) are what "material change" means.
NEW = "New"
UPGRADED = "Upgraded"
DOWNGRADED = "Downgraded"
RETRACTED = "Retracted"
AGED_OUT = "Aged-out"
CORRECTION = "Correction"
ONGOING = "Ongoing"

# Render order of the "Since the last brief" section, then the below-the-fold set.
CHANGE_BUCKETS = (NEW, UPGRADED, DOWNGRADED, RETRACTED, AGED_OUT, CORRECTION)
ALL_BUCKETS = CHANGE_BUCKETS + (ONGOING,)


def impact_tier(
    status: str | None,
    gdacs_episodealertlevel: str | None,
    pager_alert: str | None,
) -> int:
    """The impact tier used for up/downgrade comparison: the MAX severity rank
    across the GDACS episode colour and the USGS PAGER colour, both on the shared
    0/1/2 scale (green < orange < red; PAGER yellow/green share the low tier).

    Confirmation is deliberately NOT folded in here — ``status`` is accepted for a
    stable signature and future use, but the provisional->confirmed escalation is
    handled separately in ``classify`` so a re-review can never masquerade as a
    colour change (and vice versa). Missing colours contribute nothing; an event
    with no impact signal at all sits at the low tier (0)."""
    _ = status  # confirmation is scored in classify(), not the tier — see docstring
    ranks = [0]
    if gdacs_episodealertlevel:
        ranks.append(config.GDACS_ALERT_RANK.get(gdacs_episodealertlevel.lower(), 0))
    if pager_alert:
        ranks.append(config.PAGER_ALERT_RANK.get(pager_alert.lower(), 0))
    return max(ranks)


@dataclass(frozen=True)
class SnapshotEvent:
    """One canonical event as a brief asserts it — the unit both the published
    snapshot and the diff speak. ``tier`` is the derived impact tier (stored so a
    loaded prior snapshot need not recompute against today's config)."""

    canonical_id: str
    title: str | None
    magnitude: float | None
    place: str | None
    status: str | None
    gdacs_episodealertlevel: str | None
    pager_alert: str | None
    origin_time: str | None
    sources: tuple[str, ...]
    tier: int

    @classmethod
    def from_row(cls, row: EventRow) -> "SnapshotEvent":
        status = row.status or config.STATUS_PROVISIONAL
        return cls(
            canonical_id=row.canonical_id,
            title=row.title,
            magnitude=row.magnitude,
            place=row.place,
            status=status,
            gdacs_episodealertlevel=row.gdacs_episodealertlevel,
            pager_alert=row.pager_alert,
            origin_time=row.origin_time,
            sources=tuple(row.sources),
            tier=impact_tier(status, row.gdacs_episodealertlevel, row.pager_alert),
        )

    def to_dict(self) -> dict:
        return {
            "canonical_id": self.canonical_id,
            "title": self.title,
            "magnitude": self.magnitude,
            "place": self.place,
            "status": self.status,
            "gdacs_episodealertlevel": self.gdacs_episodealertlevel,
            "pager_alert": self.pager_alert,
            "origin_time": self.origin_time,
            "sources": list(self.sources),
            "tier": self.tier,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SnapshotEvent":
        return cls(
            canonical_id=data["canonical_id"],
            title=data.get("title"),
            magnitude=data.get("magnitude"),
            place=data.get("place"),
            status=data.get("status"),
            gdacs_episodealertlevel=data.get("gdacs_episodealertlevel"),
            pager_alert=data.get("pager_alert"),
            origin_time=data.get("origin_time"),
            sources=tuple(data.get("sources") or ()),
            tier=int(data.get("tier", 0)),
        )


def classify(current: SnapshotEvent, previous: SnapshotEvent | None) -> str:
    """Classify one event against its state in the last brief. Returns a bucket.

    Order matters: a positive withdrawal / window scroll-out is read off
    ``status`` FIRST (so it can never be mistaken for a colour downgrade), and
    only the TRANSITION into a terminal status is a change — an already-retracted
    (or already-aged-out) event that was terminal in the last brief is Ongoing, so
    a re-brief is idempotent ("no material change")."""
    if previous is None:
        return NEW

    if current.status == config.STATUS_RETRACTED:
        return ONGOING if previous.status == config.STATUS_RETRACTED else RETRACTED
    if current.status == config.STATUS_AGED_OUT:
        return ONGOING if previous.status == config.STATUS_AGED_OUT else AGED_OUT

    ct, pt = current.tier, previous.tier
    promoted = (
        previous.status == config.STATUS_PROVISIONAL
        and current.status == config.STATUS_CONFIRMED
        and ct >= config.MATERIAL_IMPACT_TIER
    )
    if ct > pt or promoted:
        return UPGRADED
    if ct < pt:
        return DOWNGRADED
    # Impact tier unchanged: a magnitude revision is a Correction, not a re-rank.
    if current.magnitude != previous.magnitude:
        return CORRECTION
    return ONGOING


def _fmt_mag(magnitude: float | None) -> str:
    return f"M{magnitude:.1f}" if magnitude is not None else "M?"


def describe(bucket: str, current: SnapshotEvent, previous: SnapshotEvent | None) -> str | None:
    """A deterministic, trust-building detail string for a classified event.

    Corrections/downgrades/upgrades are attributed to the FEED's revision (the
    mutability model), never phrased as the monitor's own mistake — "we said X ->
    it is now Y - the source revised it" (PRD Q5)."""
    if bucket == CORRECTION and previous is not None:
        return (
            f"we said {_fmt_mag(previous.magnitude)} -> it is now "
            f"{_fmt_mag(current.magnitude)} - the source revised it"
        )
    if bucket in (DOWNGRADED, UPGRADED) and previous is not None:
        prev_c = previous.gdacs_episodealertlevel
        cur_c = current.gdacs_episodealertlevel
        if prev_c and cur_c and prev_c.lower() != cur_c.lower():
            verb = "reassessed" if bucket == DOWNGRADED else "escalated"
            return f"we said GDACS {prev_c} -> it is now GDACS {cur_c} - the source {verb} it"
        if current.magnitude != previous.magnitude:
            return (
                f"we said {_fmt_mag(previous.magnitude)} -> it is now "
                f"{_fmt_mag(current.magnitude)} - the source revised it"
            )
        return None
    if bucket == RETRACTED:
        return "the source withdrew this event - stop acting on it"
    if bucket == AGED_OUT:
        return "no longer in the feed window - not confirmed ended"
    return None


@dataclass(frozen=True)
class ClassifiedEvent:
    """An event with its bucket and (optional) human detail."""

    event: SnapshotEvent
    bucket: str
    detail: str | None = None


@dataclass(frozen=True)
class Diff:
    """The structured, ordered result of a brief diff. ``buckets`` maps every
    bucket label to its (magnitude-ordered) list of classified events."""

    buckets: dict[str, list[ClassifiedEvent]] = field(default_factory=dict)

    def get(self, bucket: str) -> list[ClassifiedEvent]:
        return self.buckets.get(bucket, [])

    @property
    def has_material_change(self) -> bool:
        """True iff any CHANGE bucket (everything but Ongoing) is non-empty."""
        return any(self.get(b) for b in CHANGE_BUCKETS)

    def counts(self) -> dict[str, int]:
        return {b: len(self.get(b)) for b in ALL_BUCKETS}


def build_diff(
    current: list[SnapshotEvent], previous: list[SnapshotEvent] | None
) -> Diff:
    """Diff the current canonical events against the last published snapshot.

    Pure and deterministic: ``current`` is consumed in its given order (the brief
    passes it magnitude-desc), so each bucket is stably ordered. Events in the
    previous snapshot but absent from ``current`` shouldn't happen (the ledger
    never deletes rows; retraction/aged-out keep the row), but they are simply not
    emitted — defensive, never a crash."""
    prev_by_id: dict[str, SnapshotEvent] = {
        e.canonical_id: e for e in (previous or [])
    }
    buckets: dict[str, list[ClassifiedEvent]] = {b: [] for b in ALL_BUCKETS}
    for cur in current:
        prev = prev_by_id.get(cur.canonical_id)
        bucket = classify(cur, prev)
        buckets[bucket].append(
            ClassifiedEvent(event=cur, bucket=bucket, detail=describe(bucket, cur, prev))
        )
    return Diff(buckets=buckets)
