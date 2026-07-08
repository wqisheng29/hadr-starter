"""Time as an injected dependency.

The pipeline never calls ``datetime.now()`` directly — it takes a ``Clock``.
Production wires ``SystemClock``; tests and reproducible demos wire
``FrozenClock``. This is what makes the whole pipeline deterministic under a
recorded fixture without monkeypatching ``datetime`` or pulling in freezegun.
"""

from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from . import config


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Real wall-clock time, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """A fixed instant, for tests and ``--as-of`` demos."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._instant = instant.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._instant


def to_sgt(instant: datetime) -> datetime:
    """Convert a UTC (or aware) instant to Singapore time."""
    return instant.astimezone(ZoneInfo(config.SGT_TZ_NAME))


def format_sgt(instant: datetime) -> str:
    """Render an instant as e.g. ``2026-07-08 16:30 SGT``."""
    return to_sgt(instant).strftime("%Y-%m-%d %H:%M SGT")
