#!/usr/bin/env python3
"""CLI entrypoint for the slice-1 pipeline.

Thin by design: parse args, choose a feed source + clock, call ``pipeline.run``,
print a one-line summary. All behaviour lives in the ``hadr`` package.

    python scripts/run.py --fixture fixtures/usgs/all_day.json
    python scripts/run.py --live
"""

import argparse
import sys
from datetime import datetime

from hadr import config
from hadr.clock import FrozenClock, SystemClock
from hadr.fetch import FixtureFeedSource, HttpFeedSource
from hadr.pipeline import run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HADR slice-1 pipeline: USGS -> ledger -> dashboard")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", metavar="PATH", help="read a recorded USGS feed body from PATH")
    src.add_argument("--live", action="store_true", help="fetch the live USGS feed")
    parser.add_argument("--db", default=str(config.DEFAULT_DB_PATH), help="SQLite ledger path")
    parser.add_argument("--out", default=str(config.DEFAULT_OUT_PATH), help="dashboard.html output path")
    parser.add_argument("--min-magnitude", type=float, default=config.MIN_MAGNITUDE,
                        help="materiality floor (default %(default)s)")
    parser.add_argument("--as-of", metavar="ISO8601",
                        help="freeze the clock at this instant (e.g. 2026-07-08T00:30:00Z) for reproducible runs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    source = HttpFeedSource(config.USGS_URL) if args.live else FixtureFeedSource(args.fixture)

    if args.as_of:
        try:
            clock = FrozenClock(datetime.fromisoformat(args.as_of.replace("Z", "+00:00")))
        except ValueError as exc:
            print(f"✗ bad --as-of {args.as_of!r}: {exc}", file=sys.stderr)
            return 2
    else:
        clock = SystemClock()

    result = run(source, clock, db_path=args.db, out_path=args.out,
                 min_magnitude=args.min_magnitude)

    status = result.feed_status.state.value
    print(f"feed={status} rows_written={result.rows_written} "
          f"events_total={result.events_total} -> {result.out_path}")
    for warning in result.warnings:
        print(f"  note: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
