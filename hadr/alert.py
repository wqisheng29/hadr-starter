"""The urgent-alert decision — a pure, dependency-free function of ledger facts.

Slice 4's whole point (issue #7): whether a severe, high-confidence earthquake
breaks silence with a push, AND the words of that push, are decided ENTIRELY by
the deterministic core — NO LLM on this path. So this module imports only
``config`` (the thresholds), takes the event's stored facts plus an "as of"
string, and returns an ``Alert`` or ``None``. Same facts + same "as of" ⇒ same
decision + byte-identical message, every time.

The rule (thresholds in ``config``, not prose):

* **Severe** — impact, never raw magnitude: GDACS latest-episode alert is Red
  (``URGENT_GDACS_LEVELS``), or USGS PAGER colour is Orange/Red
  (``URGENT_PAGER_LEVELS``). A strong-but-unconfirmed quake NEVER fires.
* **High-confidence** — the event's lifecycle ``status`` is CONFIRMED (Slice 3).
  A pre-ShakeMap GDACS Red is still provisional ⇒ does not fire; PAGER is
  human-reviewed, so its presence both makes the event severe and confirms it.
* **Escalation-only, one-push-per-event** — the current urgent level is the
  MAX-rank qualifying signal (``URGENT_LEVEL_RANK``). Fire iff confirmed AND
  there is a current urgent level AND it is strictly higher-rank than the last
  level pushed for this event. First severe+confirmed fires; a re-run at the same
  level does not; an Orange→Red escalation fires again; a downgrade / no-longer-
  severe event does not.
"""

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class EventFacts:
    """The stored ledger facts the decision + message are built from.

    Everything here is a plain column value read back from ``canonical_events`` —
    no live signal, no enrichment. ``last_pushed_level`` is the alert level last
    pushed for this event (nullable), which is what makes one-push-per-event
    survive stateless ticks (ADR-0002)."""

    canonical_id: str
    status: str | None
    gdacs_episodealertlevel: str | None
    pager_alert: str | None
    last_pushed_level: str | None
    title: str | None = None
    magnitude: float | None = None
    place: str | None = None


@dataclass(frozen=True)
class Alert:
    """A fired urgent alert: the level, the deterministic message, and the facts
    it was built from (so a test can assert the decision as data)."""

    canonical_id: str
    level: str
    message: str
    facts: EventFacts


def _rank(level: str | None) -> int:
    """Rank of an urgent level (unknown/None ⇒ 0, below any real level)."""
    if level is None:
        return 0
    return config.URGENT_LEVEL_RANK.get(level.lower(), 0)


def _urgent_level(facts: EventFacts) -> str | None:
    """The event's current urgent level — the MAX-rank qualifying signal, or None
    if nothing about it is severe. GDACS contributes Red; PAGER contributes its
    Orange/Red colour. Compared case-insensitively via ``config``."""
    candidates: list[str] = []
    gdacs = facts.gdacs_episodealertlevel
    if gdacs and gdacs.lower() in config.URGENT_GDACS_LEVELS:
        candidates.append(gdacs.lower())
    pager = facts.pager_alert
    if pager and pager.lower() in config.URGENT_PAGER_LEVELS:
        candidates.append(pager.lower())
    if not candidates:
        return None
    return max(candidates, key=_rank)


def decide_alert(facts: EventFacts, as_of: str) -> Alert | None:
    """Decide whether to push for this event, and compose the message if so.

    Pure: depends only on ``facts``, ``as_of``, and ``config`` — never the clock,
    the network, or an LLM. Returns an ``Alert`` to fire, or ``None`` to stay
    silent."""
    level = _urgent_level(facts)
    if level is None:
        return None  # not severe -> silent (no magnitude escape hatch)
    if facts.status != config.STATUS_CONFIRMED:
        return None  # severe but not yet confirmed -> confirmation-only, silent
    if _rank(level) <= _rank(facts.last_pushed_level):
        return None  # already pushed at this level or higher -> no re-fire
    return Alert(
        canonical_id=facts.canonical_id,
        level=level,
        message=compose_message(facts, as_of),
        facts=facts,
    )


def _fmt_mag(magnitude: float | None) -> str | None:
    if magnitude is None:
        return None
    # One decimal, matching the dashboard's "M%.1f" so the operator sees the same
    # magnitude formatting on the push and the brief (M7.0, not M7).
    return f"M{magnitude:.1f}"


def compose_message(facts: EventFacts, as_of: str) -> str:
    """A fixed-format push message built ONLY from ledger facts + the "as of"
    string. Missing signals are omitted cleanly (no PAGER ⇒ no "PAGER" clause).
    Example:

        "URGENT: M6.8 earthquake — 120 km SW of Padang, Indonesia. GDACS Red;
        PAGER Orange. Confirmed, as of 2026-07-08 08:30 SGT."
    """
    mag = _fmt_mag(facts.magnitude)
    lead = f"{mag} earthquake" if mag else "Earthquake"
    where = f" — {facts.place}" if facts.place else ""

    signals: list[str] = []
    gdacs = facts.gdacs_episodealertlevel
    if gdacs and gdacs.lower() in config.URGENT_GDACS_LEVELS:
        signals.append(f"GDACS {gdacs.capitalize()}")
    pager = facts.pager_alert
    if pager and pager.lower() in config.URGENT_PAGER_LEVELS:
        signals.append(f"PAGER {pager.capitalize()}")
    signal_clause = f" {'; '.join(signals)}." if signals else ""

    return f"URGENT: {lead}{where}.{signal_clause} Confirmed, as of {as_of}."
