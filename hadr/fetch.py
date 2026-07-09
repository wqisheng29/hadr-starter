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
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from .model import FetchOutcome, GdacsParseResult, GdacsRecord, ParseResult, QuakeRecord

# Feed-identifier namespaces stored in ``feed_identifiers.source``.
USGS_SOURCE = "usgs"
GDACS_SOURCE = "gdacs"
# GLIDE is a cross-feed disaster number, not a feed of its own; it lives in the
# same table so a GLIDE match can collapse records from different feeds.
GLIDE_SOURCE = "glide"


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

    A broken *document* shape (invalid JSON, or no ``features`` list) returns
    ``ParseResult(ok=False, ...)`` rather than raising, so the caller can note an
    unparseable feed and carry on. An individual malformed *feature* is skipped
    and tallied in ``ParseResult.skipped`` — one junk record must not discard a
    whole feed of real quakes.
    """
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        return ParseResult(ok=False, error=f"invalid JSON: {exc}")

    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        return ParseResult(ok=False, error="not a GeoJSON FeatureCollection")

    records: list[QuakeRecord] = []
    skipped = 0
    for feature in doc["features"]:
        try:
            record = _parse_feature(feature)
        except (KeyError, TypeError, ValueError, IndexError):
            # Drop the bad feature, keep the good ones. A malformed record is data
            # (a skip), not a reason to reject the whole feed.
            skipped += 1
            continue
        records.append(record)

    return ParseResult(ok=True, records=tuple(records), skipped=skipped)


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
    """Coerce to float, treating null and non-finite values as absent.

    ``json.loads`` accepts the bare ``NaN``/``Infinity`` tokens, and a NaN would
    break idempotency (``nan != nan`` makes every re-run look changed) while an
    ``inf`` magnitude would sail past the materiality floor. A non-finite value is
    not a usable measurement, so it is treated as null (and thus dropped).
    """
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def parse_gdacs(body: str) -> GdacsParseResult:
    """Parse a GDACS ``EVENTS4APP`` GeoJSON body into normalised records.

    Mirrors ``parse_usgs``'s posture. A broken document *shape* (invalid JSON, no
    ``features`` list) is a whole-feed failure (``ok=False``). Below that, the
    feed degrades gracefully: a single malformed feature is skipped (counted in
    ``skipped``) rather than sinking the run, and well-formed non-earthquake
    events are filtered out (counted in ``non_eq_dropped``). Never raises.
    """
    try:
        doc = json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        return GdacsParseResult(ok=False, error=f"invalid JSON: {exc}")

    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        return GdacsParseResult(ok=False, error="not a GeoJSON FeatureCollection")

    records: list[GdacsRecord] = []
    skipped = 0
    non_eq_dropped = 0
    for feature in doc["features"]:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            skipped += 1
            continue
        if str(feature["properties"].get("eventtype", "")) != "EQ":
            non_eq_dropped += 1
            continue
        try:
            records.append(_parse_gdacs_feature(feature))
        except (KeyError, TypeError, ValueError, IndexError):
            skipped += 1

    return GdacsParseResult(
        ok=True,
        records=tuple(records),
        skipped=skipped,
        non_eq_dropped=non_eq_dropped,
    )


def _parse_gdacs_feature(feature: dict) -> GdacsRecord:
    props = feature["properties"]
    severity = props.get("severitydata") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []

    return GdacsRecord(
        eventtype=str(props["eventtype"]),
        eventid=str(props["eventid"]),
        episodeid=str(props.get("episodeid", "")),
        glide=str(props.get("glide") or ""),
        name=props.get("name"),
        alertlevel=props.get("alertlevel"),
        episodealertlevel=props.get("episodealertlevel"),
        alertscore=_maybe_float(props.get("alertscore")),
        episodealertscore=_maybe_float(props.get("episodealertscore")),
        country=props.get("country"),
        iso3=props.get("iso3"),
        source=str(props.get("source") or ""),
        sourceid=str(props.get("sourceid") or ""),
        magnitude=_maybe_float(severity.get("severity")),
        origin_time_ms=_gdacs_time_ms(props["fromdate"]),
        longitude=float(coords[0]) if len(coords) > 0 else None,
        latitude=float(coords[1]) if len(coords) > 1 else None,
        depth_km=_maybe_float(coords[2]) if len(coords) > 2 else None,
    )


def _gdacs_time_ms(value: object) -> int:
    """Parse a GDACS ``fromdate`` ISO string to epoch ms (naive == UTC)."""
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
