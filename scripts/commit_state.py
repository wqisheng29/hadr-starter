#!/usr/bin/env python3
"""Single-writer commit helper for the two schedulers (Slice 7).

The two ticks persist state to the repo (ADR-0006), and they must never clobber
each other's binary ledger. Two rules make that safe (PRD #3 write coordination):

1. **Single writer / disjoint paths.** Each role commits only the paths it OWNS
   (``config.TICK_COMMIT_PATHS`` / ``config.BRIEF_COMMIT_PATHS``): the fast tick
   owns ``state/ledger.db``; the 08:30 brief owns ``dashboard.html`` and today's
   ``state/published/<date>.json``. Because the two sets are disjoint, a rebase of
   one onto the other can never hit a content conflict on the binary DB — the
   ledger has exactly one writer.
2. **Commit only on change, then pull --rebase and retry.** Nothing is committed
   when the owned paths are unchanged (no empty commits, no binary churn). On
   push, we ``git pull --rebase`` and retry so a commit from the *other*
   scheduler landing in between is absorbed by a replay, not a clobber.

The DECISION — which paths a role owns, and whether there is anything to commit —
is pure and unit-tested (``paths_for_role`` / ``should_commit``); only the git
plumbing in ``main`` shells out.

    python scripts/commit_state.py --role tick  -m "chore(tick): reconcile ledger"
    python scripts/commit_state.py --role brief -m "chore(brief): 08:30 sitrep"
"""

import argparse
import subprocess
import sys

from hadr import config

ROLE_TICK = "tick"
ROLE_BRIEF = "brief"

_OWNED: dict[str, tuple[str, ...]] = {
    ROLE_TICK: config.TICK_COMMIT_PATHS,
    ROLE_BRIEF: config.BRIEF_COMMIT_PATHS,
}


def paths_for_role(role: str) -> tuple[str, ...]:
    """The repo paths a role is the single writer of. Raises on an unknown role
    so a typo can never widen a writer's blast radius to another's paths."""
    try:
        return _OWNED[role]
    except KeyError:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(_OWNED)}") from None


def _is_under(path: str, roots: tuple[str, ...]) -> bool:
    """True if ``path`` is one of ``roots`` or nested beneath one (so a snapshot
    ``state/published/2026-07-09.json`` counts as owned by ``state/published``)."""
    norm = path.strip().strip("/")
    for root in roots:
        r = root.strip("/")
        if norm == r or norm.startswith(r + "/"):
            return True
    return False


def should_commit(changed_paths: set[str], role: str) -> bool:
    """Commit iff at least one path this role OWNS actually changed. A change to a
    path owned by the *other* role is ignored here — the disjoint-path discipline
    that keeps the two schedulers from clobbering each other, enforced in code."""
    owned = paths_for_role(role)
    return any(_is_under(p, owned) for p in changed_paths)


# --- git plumbing (impure; the decision above is what the tests exercise) ------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _changed_paths(owned: tuple[str, ...]) -> set[str]:
    """The owned paths that differ from HEAD (staged or unstaged), via
    ``git status --porcelain`` scoped to those paths."""
    result = _run(["git", "status", "--porcelain", "--", *owned])
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        # porcelain v1: XY<space>path  (rename "old -> new" — take the new name).
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.add(entry)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Commit this scheduler's owned state paths, only on change.")
    parser.add_argument("--role", required=True, choices=sorted(_OWNED),
                        help="which scheduler is committing (defines the owned paths)")
    parser.add_argument("-m", "--message", required=True, help="commit message")
    parser.add_argument("--retries", type=int, default=3,
                        help="pull --rebase + push attempts before giving up (default %(default)s)")
    parser.add_argument("--no-push", action="store_true",
                        help="commit locally but do not push (for local/dry runs)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    owned = paths_for_role(args.role)
    changed = _changed_paths(owned)
    if not should_commit(changed, args.role):
        print(f"commit_state[{args.role}]: no change in owned paths {list(owned)} — nothing to commit")
        return 0

    # Stage the specific changed paths (not the owned globs): an owned path that
    # does not exist yet — e.g. state/published before the first brief — would make
    # `git add -- <owned>` abort the whole add on the missing pathspec.
    _run(["git", "add", "--", *sorted(changed)])
    commit = _run(["git", "commit", "-m", args.message])
    if commit.returncode != 0:
        print(f"✗ git commit failed: {commit.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"commit_state[{args.role}]: committed {sorted(changed)}")

    if args.no_push:
        return 0

    for attempt in range(1, args.retries + 1):
        # Rebase our commit on top of anything the other scheduler pushed since
        # checkout (disjoint paths -> no conflict), then push.
        rebase = _run(["git", "pull", "--rebase"])
        if rebase.returncode != 0:
            print(f"✗ git pull --rebase failed (attempt {attempt}): {rebase.stderr.strip()}",
                  file=sys.stderr)
            return 1
        push = _run(["git", "push"])
        if push.returncode == 0:
            print(f"commit_state[{args.role}]: pushed on attempt {attempt}")
            return 0
        print(f"  push rejected (attempt {attempt}/{args.retries}); retrying after rebase",
              file=sys.stderr)
    print("✗ push failed after retries", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
