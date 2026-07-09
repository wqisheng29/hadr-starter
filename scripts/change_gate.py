#!/usr/bin/env python3
"""CLI for the deterministic change-gate (Slice 7).

Thin by design, mirroring scripts/brief.py: parse args, build a clock, call
``gate.evaluate``, and emit a branchable verdict. It calls NO model and touches
NO network — it is the cheap check that decides whether the expensive 08:30 brief
should wake at all.

Two branchable outputs, so it drops into either a workflow or a shell:

* It writes ``changed=true|false`` to ``$GITHUB_OUTPUT`` (when set), so a workflow
  step guards on ``if: steps.<id>.outputs.changed == 'true'`` and the publish step
  is *visibly skipped* on a no-change run.
* With ``--exit-code`` it returns 0 when changed / 1 when not, for ``&&`` chaining
  in a plain shell.

    python scripts/change_gate.py --db state/ledger.db --out dashboard.html \
        --published-dir state/published
"""

import argparse
import os
import sys
from datetime import datetime

from hadr import config, gate
from hadr.clock import FrozenClock, SystemClock


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic change-gate: has the 08:30 brief anything material to republish?"
    )
    parser.add_argument("--db", default=str(config.DEFAULT_DB_PATH),
                        help="SQLite ledger path (read-only)")
    parser.add_argument("--out", default=str(config.DEFAULT_OUT_PATH),
                        help="dashboard.html path; a missing artifact counts as change (bootstrap)")
    parser.add_argument("--published-dir", default=str(config.DEFAULT_PUBLISHED_DIR),
                        help="directory of readable published snapshots to diff against")
    parser.add_argument("--as-of", metavar="ISO8601",
                        help="freeze the clock at this instant for a reproducible decision")
    parser.add_argument("--exit-code", action="store_true",
                        help="exit 0 if changed, 1 if not (for shell && chaining)")
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

    decision = gate.evaluate(args.db, args.published_dir, clock, dashboard_path=args.out)

    changes = ", ".join(f"{b}={decision.counts[b]}" for b in decision.counts if b != "Ongoing")
    print(f"gate: {decision.reason} [{changes}] -> {decision.flag}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(decision.flag + "\n")

    if args.exit_code:
        return 0 if decision.changed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
