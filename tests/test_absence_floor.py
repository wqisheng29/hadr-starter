"""An event revised below the materiality floor is still PRESENT in the feed, so
absence detection must not falsely retract/age it. Guards the pipeline `seen` set
being computed over all parsed records, not just the qualifying (>= floor) ones.
"""

from hadr.ledger import connect
from hadr.model import FetchOutcome
from hadr.pipeline import run


class _Body:
    """A FeedSource returning a fixed body (no fixture file, no network)."""

    def __init__(self, body: str) -> None:
        self._body = body

    def fetch(self) -> FetchOutcome:
        return FetchOutcome(ok=True, body=self._body)


def _usgs(mag: float) -> str:
    return (
        '{"type":"FeatureCollection","features":[{"type":"Feature","id":"us9",'
        f'"properties":{{"mag":{mag},"place":"p","time":1783300000000,'
        '"updated":1783300000000,"status":"reviewed","ids":",us9,","title":"T"},'
        '"geometry":{"type":"Point","coordinates":[1.0,2.0,10.0]}}]}'
    )


def _status(db_path, canonical_id):
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM canonical_events WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def test_below_floor_revision_is_not_falsely_retracted(frozen_clock, tmp_ledger, tmp_out):
    # Tick 1: M5.0 -> stored (confirmed, reviewed).
    run(_Body(_usgs(5.0)), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, "usgs:us9") == "confirmed"

    # Tick 2: same event revised to M4.0 (below the 4.5 floor) but STILL in the feed.
    # It is dropped from reconcile, but it was seen -> must NOT be retracted/aged_out.
    run(_Body(_usgs(4.0)), frozen_clock, tmp_ledger, tmp_out)
    assert _status(tmp_ledger, "usgs:us9") == "confirmed"  # unchanged, not withdrawn
