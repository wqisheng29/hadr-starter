"""Normalised data shapes that flow through the deterministic core.

Everything downstream of ``parse_usgs`` speaks these types, not raw feed JSON —
this is the seam the tests inject at (PRD "Seam 1", the feed-fetch boundary).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


@dataclass(frozen=True)
class QuakeRecord:
    """One normalised earthquake, feed-agnostic below this point.

    ``ids`` is the union of every identifier the feed carried for this quake
    (USGS packs several into ``properties.ids``); it is what lets a quake keep
    the same canonical event even when its preferred ``id`` changes.
    """

    preferred_id: str
    ids: frozenset[str]
    magnitude: float | None
    place: str | None
    title: str | None
    origin_time_ms: int
    updated_ms: int
    longitude: float
    latitude: float
    depth_km: float | None

    @property
    def origin_time_utc(self) -> datetime:
        return datetime.fromtimestamp(self.origin_time_ms / 1000, tz=timezone.utc)


@dataclass(frozen=True)
class FetchOutcome:
    """Result of asking a ``FeedSource`` for the feed. Never raises to callers."""

    ok: bool
    body: str | None = None
    status: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing a feed body: either records, or a parse error.

    ``skipped`` counts individual features that were malformed and dropped while
    the rest of the feed parsed successfully. A single junk feature must not sink
    a whole feed of real quakes (failures are data, at feature granularity too),
    so a per-feature error is counted here, not raised as ``ok=False``. Only a
    broken *document* shape (invalid JSON, no ``features`` list) is ``ok=False``.
    """

    ok: bool
    records: tuple[QuakeRecord, ...] = ()
    error: str | None = None
    skipped: int = 0


class FeedState(Enum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    UNPARSEABLE = "unparseable"


@dataclass(frozen=True)
class FeedStatus:
    """How the feed behaved this run — surfaced in the dashboard banner."""

    source: str
    state: FeedState
    detail: str | None = None

    @classmethod
    def ok(cls, source: str) -> "FeedStatus":
        return cls(source, FeedState.OK)

    @classmethod
    def unreachable(cls, source: str, detail: str) -> "FeedStatus":
        return cls(source, FeedState.UNREACHABLE, detail)

    @classmethod
    def unparseable(cls, source: str, detail: str) -> "FeedStatus":
        return cls(source, FeedState.UNPARSEABLE, detail)

    @property
    def is_ok(self) -> bool:
        return self.state is FeedState.OK


@dataclass(frozen=True)
class EventRow:
    """A canonical-event row as read back for rendering."""

    canonical_id: str
    title: str | None
    magnitude: float | None
    place: str | None
    origin_time: str | None  # ISO8601 UTC


@dataclass(frozen=True)
class RunResult:
    """Summary of a pipeline run, for the CLI to print."""

    feed_status: FeedStatus
    rows_written: int
    events_total: int
    out_path: str
    warnings: tuple[str, ...] = field(default=())
