"""Orchestration: fetch -> parse -> filter -> reconcile -> render.

Pure with respect to the outside world: the feed and the clock are injected, so
the same fixture + a frozen clock always produce the same ledger and the same
``dashboard.html``. The dashboard is *always* rendered — a feed that is
unreachable or unparseable is noted in a banner over the last known picture,
never a crash and never a wiped ledger.
"""

from pathlib import Path

from . import config
from .briefer import write_dashboard
from .clock import Clock
from .fetch import USGS_SOURCE, FeedSource, parse_usgs
from .ledger import connect, read_events, reconcile
from .model import FeedStatus, QuakeRecord, RunResult


def _qualifies(record: QuakeRecord, min_magnitude: float) -> bool:
    # A null magnitude cannot clear the floor -> dropped.
    return record.magnitude is not None and record.magnitude >= min_magnitude


def run(
    source: FeedSource,
    clock: Clock,
    db_path: str | Path = config.DEFAULT_DB_PATH,
    out_path: str | Path = config.DEFAULT_OUT_PATH,
    min_magnitude: float = config.MIN_MAGNITUDE,
) -> RunResult:
    conn = connect(db_path)
    try:
        rows_written = 0
        warnings: list[str] = []

        outcome = source.fetch()
        if not outcome.ok:
            detail = outcome.error or f"HTTP {outcome.status}"
            feed_status = FeedStatus.unreachable(USGS_SOURCE, detail)
            warnings.append(f"feed unreachable ({detail}); ledger unchanged")
        else:
            parsed = parse_usgs(outcome.body or "")
            if not parsed.ok:
                feed_status = FeedStatus.unparseable(USGS_SOURCE, parsed.error or "parse error")
                warnings.append(f"feed unparseable ({parsed.error}); ledger unchanged")
            else:
                feed_status = FeedStatus.ok(USGS_SOURCE)
                if parsed.skipped:
                    warnings.append(f"skipped {parsed.skipped} malformed feature(s)")
                qualifying = [r for r in parsed.records if _qualifies(r, min_magnitude)]
                dropped = len(parsed.records) - len(qualifying)
                if dropped:
                    warnings.append(f"dropped {dropped} sub-M{min_magnitude} quake(s)")
                rows_written = reconcile(conn, qualifying, clock)

        events = read_events(conn)
        write_dashboard(events, clock.now(), feed_status, out_path)

        return RunResult(
            feed_status=feed_status,
            rows_written=rows_written,
            events_total=len(events),
            out_path=str(out_path),
            warnings=tuple(warnings),
        )
    finally:
        conn.close()
