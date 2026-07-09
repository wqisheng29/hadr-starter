#!/usr/bin/env python3
"""CLI entrypoint for the 08:30 brief (Slices 5-6).

Thin by design, mirroring scripts/run.py: parse args, build a clock, call
``brief.write_brief``, print a one-line summary. It does NOT fetch or reconcile —
the brief only reads ``ledger.db`` (read-only) plus the last published snapshot,
and writes today's snapshot + dashboard.html. Run the tick (scripts/run.py) first
to build the ledger.

    python scripts/brief.py --db state/ledger.db --out dashboard.html \
        --published-dir state/published --as-of 2026-07-08T00:30:00Z

Slice 6 (optional, injected edges — never required, so a keyless run still
briefs deterministically):

* ``--model`` turns on the LLM judgement layer (impact prose + fuzzy ReliefWeb
  tie-break); it reads ``OPENCODE_API_KEY`` from the env (see hadr/llm.py). A
  model error degrades to the deterministic basis — it never crashes the brief.
* ``--reliefweb PATH`` loads a recorded ReliefWeb payload (this slice does not
  fetch it live) for excerpt + link + attribution.
* ``--link-decisions-out PATH`` writes the fuzzy-link decisions the NEXT tick
  applies (disjoint from ledger.db; defaults to config's path).
"""

import argparse
import sys
from datetime import datetime

from hadr import config
from hadr.brief import write_brief
from hadr.clock import FrozenClock, SystemClock
from hadr.reliefweb import load_fixture


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
    parser.add_argument("--model", nargs="?", const="", metavar="MODEL_ID",
                        help="enable the LLM judgement layer (impact prose + fuzzy ReliefWeb "
                             "tie-break); optionally name a model, else OPENCODE_MODEL/config default")
    parser.add_argument("--reliefweb", metavar="PATH",
                        help="recorded ReliefWeb payload (JSON) for excerpt + link + attribution")
    parser.add_argument("--link-decisions-out", default=str(config.DEFAULT_LINK_DECISIONS_PATH),
                        metavar="PATH", help="where to write fuzzy-link decisions for the next tick")
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

    # The model is an optional injected edge. Building it can fail (no key); that
    # is a setup error worth reporting, not a silent keyless run.
    model = None
    if args.model is not None:
        try:
            from hadr.llm import from_env
            model = from_env(model=args.model or None)
        except RuntimeError as exc:
            print(f"✗ --model requested but {exc}", file=sys.stderr)
            return 2

    reliefweb = load_fixture(args.reliefweb) if args.reliefweb else None

    # Fuzzy link decisions come only from the model tie-break, so only emit the
    # (disjoint) decisions file when the model is on — a deterministic keyless
    # brief writes no decisions file and touches no extra paths.
    decisions_out = args.link_decisions_out if model is not None else None

    result = write_brief(
        args.db, args.out, args.published_dir, clock,
        model=model, reliefweb=reliefweb, link_decisions_out=decisions_out,
    )

    changes = ", ".join(f"{b}={result.counts[b]}" for b in result.counts if b != "Ongoing")
    decided = f", {len(result.link_decisions)} link decision(s)" if result.link_decisions else ""
    print(
        f"brief: {'no material change' if result.no_material_change else changes} "
        f"-> {result.out_path} (snapshot {result.snapshot_path}){decided}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
