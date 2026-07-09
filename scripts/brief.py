#!/usr/bin/env python3
"""CLI entrypoint for the 08:30 brief (Slice 5).

Thin by design, mirroring scripts/run.py: parse args, build a clock, call
``brief.write_brief``, print a one-line summary. It does NOT fetch or reconcile —
the brief only reads ``ledger.db`` (read-only) plus the last published snapshot,
and writes today's snapshot + dashboard.html. Run the tick (scripts/run.py) first
to build the ledger.

    python scripts/brief.py --db state/ledger.db --out dashboard.html \
        --published-dir state/published --as-of 2026-07-08T00:30:00Z
"""

import argparse
import sys
from datetime import datetime

from hadr import config
from hadr.brief import write_brief
from hadr.clock import FrozenClock, SystemClock


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HADR 08:30 brief: ledger + last snapshot -> since-last-brief diff -> dashboard"
    )
    parser.add_argument("--db", default=str(config.DEFAULT_DB_PATH), help="SQLite ledger path (read-only)")
    parser.add_argument("--out", default=str(config.DEFAULT_OUT_PATH), help="dashboard.html output path")
    parser.add_argument("--published-dir", default=str(config.DEFAULT_PUBLISHED_DIR),
                        help="directory of readable published snapshots")
    parser.add_argument("--as-of", metavar="ISO8601",
                        help="freeze the clock at this instant (e.g. 2026-07-08T00:30:00Z) for reproducible briefs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.as_of:
        try:
            clock = FrozenClock(datetime.fromisoformat(args.as_of.replace("Z", "+00:00")))
        except ValueError as exc:
            print(f"✗ bad --as-of {args.as_of!r}: {exc}", file=sys.stderr)
            return 2
    else:
        clock = SystemClock()

    result = write_brief(args.db, args.out, args.published_dir, clock)

    changes = ", ".join(f"{b}={result.counts[b]}" for b in result.counts if b != "Ongoing")
    print(
        f"brief: {'no material change' if result.no_material_change else changes} "
        f"-> {result.out_path} (snapshot {result.snapshot_path})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
