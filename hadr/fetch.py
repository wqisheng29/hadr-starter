"""The feed-fetch boundary.

Two concerns, deliberately separated:

* ``FeedSource`` — *where the bytes come from* (network vs. a recorded fixture).
  Injected into the pipeline so tests never touch the network.
* ``parse_usgs`` — *turning bytes into normalised records*. A pure function, so
  "the feed was unparseable" is testable with no I/O at all.

Neither ever raises to the pipeline: transport and parse failures come back as
data (``FetchOutcome``/``ParseResult``), which is what lets the run degrade
gracefully instead of crashing.
"""

import json
from pathlib import Path
from typing import Protocol

import httpx

from .model import FetchOutcome, ParseResult, QuakeRecord

USGS_SOURCE = "usgs"


class FeedSource(Protocol):
    def fetch(self) -> FetchOutcome:
        ...


class HttpFeedSource:
    """Fetches the live feed over HTTP. Maps every failure to a FetchOutcome."""

    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        self._url = url
        # follow_redirects: the feed hosts redirect to their canonical host, and
        # a non-following client silently gets an empty body
        # (docs/solutions/2026-07-06-example-follow-redirects.md).
        self._client = client or httpx.Client(follow_redirects=True, timeout=30.0)

    def fetch(self) -> FetchOutcome:
        try:
            resp = self._client.get(self._url)
        except httpx.HTTPError as exc:
            return FetchOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
        if resp.status_code != 200:
            return FetchOutcome(ok=False, status=resp.status_code)
        return FetchOutcome(ok=True, body=resp.text, status=resp.status_code)


class FixtureFeedSource:
    """Reads a recorded feed body off disk — the offline, deterministic path."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def fetch(self) -> FetchOutcome:
        try:
            return FetchOutcome(ok=True, body=self._path.read_text(encoding="utf-8"))
        except OSError as exc:
            return FetchOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")


def _parse_ids(raw: object, preferred_id: str) -> frozenset[str]:
    """USGS packs ids as a comma-padded CSV, e.g. ",ci41287863,us6000tafd,".

    Split on commas, drop the empty tokens the padding produces, and always
    include the preferred id.
    """
    ids: set[str] = {preferred_id} if preferred_id else set()
    if isinstance(raw, str):
        ids.update(tok for tok in raw.split(",") if tok)
    return frozenset(ids)


def parse_usgs(body: str) -> ParseResult:
    """Parse a USGS ``all_day.geojson`` body into normalised records.

    Returns ``ParseResult(ok=False, ...)`` on anything malformed rather than
    raising, so the caller can note an unparseable feed and carry on.
    """
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        return ParseResult(ok=False, error=f"invalid JSON: {exc}")

    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        return ParseResult(ok=False, error="not a GeoJSON FeatureCollection")

    records: list[QuakeRecord] = []
    for feature in doc["features"]:
        try:
            record = _parse_feature(feature)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            return ParseResult(ok=False, error=f"malformed feature: {exc}")
        records.append(record)

    return ParseResult(ok=True, records=tuple(records))


def _parse_feature(feature: object) -> QuakeRecord:
    if not isinstance(feature, dict):
        raise TypeError("feature is not an object")
    props = feature["properties"]
    coords = feature["geometry"]["coordinates"]
    preferred_id = str(feature["id"])

    return QuakeRecord(
        preferred_id=preferred_id,
        ids=_parse_ids(props.get("ids"), preferred_id),
        magnitude=_maybe_float(props.get("mag")),
        place=props.get("place"),
        title=props.get("title"),
        origin_time_ms=int(props["time"]),
        updated_ms=int(props.get("updated", props["time"])),
        longitude=float(coords[0]),
        latitude=float(coords[1]),
        depth_km=_maybe_float(coords[2]) if len(coords) > 2 else None,
    )


def _maybe_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
