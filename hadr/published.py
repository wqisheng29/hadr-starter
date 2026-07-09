"""Readable published snapshots — the audit trail the brief diffs against.

Each 08:30 brief writes ``state/published/<SGT-date>.json``: a human-readable
record (indent=2, sorted keys) of exactly what that brief asserted, so "what
changed since yesterday" is a diff of today's ledger against the last such file,
and a reviewer can inspect past briefs even though ``ledger.db`` diffs opaquely
in git (PRD user story 33, ADR-0006). ``state/`` is git-ignored for now (a
produced artifact until Slice-7 commit coordination — see implementation-notes).

Pure-ish: the clock is injected (no ``datetime.now()``), so the same ledger +
frozen clock yields a byte-identical snapshot file, and reading is deterministic
(the latest prior filename, chosen lexicographically).
"""

import json
from pathlib import Path

from .clock import Clock, format_sgt, to_sgt
from .diff import SnapshotEvent

SCHEMA_VERSION = 1


def _sgt_date(clock: Clock) -> str:
    return to_sgt(clock.now()).strftime("%Y-%m-%d")


def write_snapshot(
    published_dir: str | Path, clock: Clock, events: list[SnapshotEvent]
) -> Path:
    """Write today's snapshot and return its path. Events are sorted by
    canonical_id so the file is stable regardless of ledger read order; the whole
    document is ``sort_keys`` + indented for a clean git/readable diff."""
    directory = Path(published_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_sgt_date(clock)}.json"
    doc = {
        "schema_version": SCHEMA_VERSION,
        "as_of": {
            "sgt": format_sgt(clock.now()),
            "utc": clock.now().isoformat(),
        },
        "events": [
            e.to_dict() for e in sorted(events, key=lambda e: e.canonical_id)
        ],
    }
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_latest_snapshot(
    published_dir: str | Path, before: str | None = None
) -> list[SnapshotEvent] | None:
    """Load the most recent PRIOR snapshot's events, or None if there is none.

    Deterministic: the lexicographically greatest ``*.json`` filename strictly
    less than ``before`` (today's SGT date). Excluding today means a brief never
    diffs against the snapshot it is about to write — "the last brief" is always a
    previous one, which also keeps a same-day re-render byte-identical."""
    directory = Path(published_dir)
    if not directory.is_dir():
        return None
    names = sorted(p for p in directory.glob("*.json"))
    candidates = [p for p in names if before is None or p.stem < before]
    if not candidates:
        return None
    return _parse(candidates[-1])


def _parse(path: Path) -> list[SnapshotEvent]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [SnapshotEvent.from_dict(e) for e in doc.get("events", [])]
