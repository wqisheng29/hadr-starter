"""Shared test fixtures: the frozen-clock + recorded-feed seam.

This is the house test style for the whole project — everything downstream of a
parsed feed is exercised with an injected ``FrozenClock`` and a
``FixtureFeedSource``, so tests are deterministic and never touch the network.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hadr.clock import FrozenClock
from hadr.fetch import FixtureFeedSource

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURES = _FIXTURE_ROOT / "usgs"
FIXTURES_GDACS = _FIXTURE_ROOT / "gdacs"

# 2026-07-08 00:30:00 UTC == 08:30 SGT — the briefing hour.
_FROZEN_INSTANT = datetime(2026, 7, 8, 0, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_FROZEN_INSTANT)


@pytest.fixture
def tmp_ledger(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    return tmp_path / "dashboard.html"


@pytest.fixture
def fixture_source():
    def _make(name: str) -> FixtureFeedSource:
        return FixtureFeedSource(FIXTURES / name)

    return _make


@pytest.fixture
def gdacs_fixture_source():
    def _make(name: str) -> FixtureFeedSource:
        return FixtureFeedSource(FIXTURES_GDACS / name)

    return _make
