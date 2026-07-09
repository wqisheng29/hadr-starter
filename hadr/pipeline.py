"""Orchestration: fetch -> parse -> filter -> reconcile -> render.

Pure with respect to the outside world: the feed and the clock are injected, so
the same fixture + a frozen clock always produce the same ledger and the same
``dashboard.html``. The dashboard is *always* rendered — a feed that is
unreachable or unparseable is noted in a banner over the last known picture,
never a crash and never a wiped ledger.
"""

from pathlib import Path

from . import config
from .alert import Alert, decide_alert
from .briefer import write_dashboard
from .clock import Clock, format_sgt
from .fetch import GDACS_SOURCE, USGS_SOURCE, FeedSource, parse_gdacs, parse_usgs
from .ledger import (
    connect,
    read_event_facts,
    read_events,
    reconcile,
    reconcile_gdacs,
    record_pushed,
)
from .model import FeedStatus, QuakeRecord, RunResult
from .push import PushSink


def _qualifies(record: QuakeRecord, min_magnitude: float) -> bool:
    # A null magnitude cannot clear the floor -> dropped.
    return record.magnitude is not None and record.magnitude >= min_magnitude


def run(
    source: FeedSource,
    clock: Clock,
    db_path: str | Path = config.DEFAULT_DB_PATH,
    out_path: str | Path = config.DEFAULT_OUT_PATH,
    min_magnitude: float = config.MIN_MAGNITUDE,
    gdacs_source: FeedSource | None = None,
    push_sink: PushSink | None = None,
) -> RunResult:
    """Fetch -> parse -> reconcile USGS, then optionally the same for GDACS onto
    the same ledger, then (if a ``push_sink`` is injected) evaluate the urgent-
    alert decision and render. Each feed degrades independently: an
    unreachable/unparseable feed is noted in a banner and the dashboard still
    renders over the last known picture.

    The urgent push is the "fast tick" (Slice 4): after both feeds reconcile and
    the lifecycle status is refreshed, every event is run through the deterministic
    ``decide_alert``; each fired ``Alert`` is delivered once and its level recorded
    so a stateless re-run does not re-fire. When ``push_sink is None`` (e.g. the
    08:30 brief context, which must not push per the PRD hybrid split) the decision
    is not even evaluated."""
    conn = connect(db_path)
    try:
        warnings: list[str] = []

        feed_status, usgs_rows = _run_usgs(conn, source, clock, min_magnitude, warnings)
        feed_statuses = [feed_status]
        rows_written = usgs_rows

        if gdacs_source is not None:
            gdacs_status, gdacs_rows = _run_gdacs(conn, gdacs_source, clock, warnings)
            feed_statuses.append(gdacs_status)
            rows_written += gdacs_rows

        alerts_pushed = _run_push(conn, push_sink, clock) if push_sink is not None else ()

        events = read_events(conn)
        write_dashboard(events, clock.now(), feed_status, out_path, feed_statuses)

        return RunResult(
            feed_status=feed_status,
            rows_written=rows_written,
            events_total=len(events),
            out_path=str(out_path),
            warnings=tuple(warnings),
            feed_statuses=tuple(feed_statuses),
            alerts_pushed=alerts_pushed,
        )
    finally:
        conn.close()


def _run_push(conn, push_sink: PushSink, clock: Clock) -> tuple[Alert, ...]:
    """Evaluate the urgent-alert decision over every event and deliver each fired
    alert exactly once. Pure decision (``decide_alert``) + one side effect per fire
    (``push_sink.send`` + ``record_pushed``). The message's "as of" comes from the
    injected clock, so the whole thing is reproducible under a frozen clock."""
    as_of = format_sgt(clock.now())
    fired: list[Alert] = []
    for facts in read_event_facts(conn):
        alert = decide_alert(facts, as_of)
        if alert is None:
            continue
        push_sink.send(alert)
        record_pushed(conn, alert.canonical_id, alert.level, clock)
        fired.append(alert)
    conn.commit()
    return tuple(fired)


def _run_usgs(conn, source, clock, min_magnitude, warnings) -> tuple[FeedStatus, int]:
    outcome = source.fetch()
    if not outcome.ok:
        detail = outcome.error or f"HTTP {outcome.status}"
        warnings.append(f"feed unreachable ({detail}); ledger unchanged")
        return FeedStatus.unreachable(USGS_SOURCE, detail), 0

    parsed = parse_usgs(outcome.body or "")
    if not parsed.ok:
        warnings.append(f"feed unparseable ({parsed.error}); ledger unchanged")
        return FeedStatus.unparseable(USGS_SOURCE, parsed.error or "parse error"), 0

    if parsed.skipped:
        warnings.append(f"skipped {parsed.skipped} malformed feature(s)")
    qualifying = [r for r in parsed.records if _qualifies(r, min_magnitude)]
    dropped = len(parsed.records) - len(qualifying)
    if dropped:
        warnings.append(f"dropped {dropped} sub-M{min_magnitude} quake(s)")
    return FeedStatus.ok(USGS_SOURCE), reconcile(conn, qualifying, clock)


def _run_gdacs(conn, source, clock, warnings) -> tuple[FeedStatus, int]:
    outcome = source.fetch()
    if not outcome.ok:
        detail = outcome.error or f"HTTP {outcome.status}"
        warnings.append(f"GDACS feed unreachable ({detail}); ledger unchanged")
        return FeedStatus.unreachable(GDACS_SOURCE, detail), 0

    parsed = parse_gdacs(outcome.body or "")
    if not parsed.ok:
        warnings.append(f"GDACS feed unparseable ({parsed.error}); ledger unchanged")
        return FeedStatus.unparseable(GDACS_SOURCE, parsed.error or "parse error"), 0

    if parsed.skipped:
        warnings.append(f"skipped {parsed.skipped} malformed GDACS feature(s)")
    if parsed.non_eq_dropped:
        warnings.append(f"dropped {parsed.non_eq_dropped} non-earthquake GDACS event(s)")
    return FeedStatus.ok(GDACS_SOURCE), reconcile_gdacs(conn, list(parsed.records), clock)
