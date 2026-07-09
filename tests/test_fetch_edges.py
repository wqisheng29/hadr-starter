"""Parser robustness: one malformed feature must not sink the whole feed, and
non-finite magnitudes are treated as absent. These guard the "failures are data"
rule at feature granularity and the idempotency contract against NaN.
"""

import math

import pytest

from hadr.fetch import parse_usgs

_GOOD = (
    '{"type":"Feature","id":"us1",'
    '"properties":{"mag":6.1,"place":"p","time":1,"updated":2,"ids":",us1,","title":"T"},'
    '"geometry":{"type":"Point","coordinates":[1.0,2.0,3.0]}}'
)


def _collection(*features: str) -> str:
    return '{"type":"FeatureCollection","features":[' + ",".join(features) + "]}"


def test_one_malformed_feature_is_skipped_not_fatal():
    # A feature missing "time" among two good ones: keep the good, count the bad.
    bad = (
        '{"type":"Feature","id":"bad",'
        '"properties":{"mag":5.0,"place":"q","ids":",bad,","title":"U"},'
        '"geometry":{"type":"Point","coordinates":[4.0,5.0,6.0]}}'
    )
    good2 = _GOOD.replace('"us1"', '"us2"').replace(',us1,', ',us2,')

    result = parse_usgs(_collection(_GOOD, bad, good2))

    assert result.ok  # the document shape is fine
    assert result.skipped == 1
    ids = {r.preferred_id for r in result.records}
    assert ids == {"us1", "us2"}  # both good records survive; bad one dropped


def test_all_features_malformed_degrades_to_zero_records():
    bad = '{"type":"Feature","id":"bad","properties":{},"geometry":{}}'
    result = parse_usgs(_collection(bad, bad))
    assert result.ok  # still a valid document, just no usable records
    assert result.records == ()
    assert result.skipped == 2


def test_broken_document_shape_still_fails():
    # Top-level shape errors remain hard failures (not per-feature skips).
    assert not parse_usgs("not json").ok
    assert not parse_usgs('{"nope": true}').ok
    assert parse_usgs("not json").skipped == 0


@pytest.mark.parametrize(
    "raw_mag, expected",
    [("6.1", 6.1), (4, 4.0), (None, None), (float("nan"), None)],
)
def test_magnitude_coercion_edges(raw_mag, expected):
    # mag arrives as a numeric string, an int, null, or (via bare NaN) non-finite.
    if isinstance(raw_mag, float) and math.isnan(raw_mag):
        body = _collection(
            '{"type":"Feature","id":"n","properties":{"mag":NaN,"place":"p",'
            '"time":1,"updated":2,"ids":",n,","title":"T"},'
            '"geometry":{"type":"Point","coordinates":[1.0,2.0,3.0]}}'
        )
    else:
        mag_json = "null" if raw_mag is None else f'"{raw_mag}"' if isinstance(raw_mag, str) else raw_mag
        body = _collection(
            '{"type":"Feature","id":"n","properties":{"mag":' + str(mag_json) +
            ',"place":"p","time":1,"updated":2,"ids":",n,","title":"T"},'
            '"geometry":{"type":"Point","coordinates":[1.0,2.0,3.0]}}'
        )
    result = parse_usgs(body)
    assert result.ok
    assert result.records[0].magnitude == expected


def test_missing_geometry_or_coords_is_skipped():
    no_geom = '{"type":"Feature","id":"g","properties":{"mag":5.0,"time":1,"ids":",g,"}}'
    short_coords = (
        '{"type":"Feature","id":"h",'
        '"properties":{"mag":5.0,"place":"p","time":1,"updated":2,"ids":",h,","title":"T"},'
        '"geometry":{"type":"Point","coordinates":[1.0]}}'
    )
    result = parse_usgs(_collection(no_geom, short_coords, _GOOD))
    assert result.ok
    assert result.skipped == 2
    assert {r.preferred_id for r in result.records} == {"us1"}


def test_two_dim_coords_leave_depth_none():
    two_d = (
        '{"type":"Feature","id":"d",'
        '"properties":{"mag":5.0,"place":"p","time":1,"updated":2,"ids":",d,","title":"T"},'
        '"geometry":{"type":"Point","coordinates":[1.0,2.0]}}'
    )
    result = parse_usgs(_collection(two_d))
    assert result.ok and result.records[0].depth_km is None
