"""Parser tests for the GDACS EVENTS4APP body. Pure, no I/O."""

from datetime import timezone

from hadr.fetch import parse_gdacs

_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "gdacs"


def _body(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


def test_parse_gdacs_normalises_fields():
    result = parse_gdacs(_body("eq_padang.json"))
    assert result.ok
    assert len(result.records) == 1
    rec = result.records[0]

    assert rec.eventtype == "EQ"
    assert rec.eventid == "1550999"          # coerced from JSON int
    assert rec.episodeid == "1716999"
    assert rec.source == "NEIC"
    assert rec.sourceid == "us7000abcd"      # == the USGS id
    assert rec.glide == ""
    assert rec.magnitude == 6.8              # from severitydata.severity
    assert rec.alertlevel == "Orange"
    assert rec.episodealertlevel == "Orange"
    assert rec.longitude == 99.6 and rec.latitude == -1.4
    assert rec.depth_km is None              # GDACS geometry carries no depth
    # fromdate parsed to tz-aware UTC epoch ms.
    assert rec.origin_time_utc.tzinfo is timezone.utc
    assert rec.origin_time_utc.year == 2026


def test_parse_gdacs_reads_glide_and_multiple_records():
    result = parse_gdacs(_body("eq_two_glide.json"))
    assert result.ok
    assert len(result.records) == 2
    assert {r.glide for r in result.records} == {"EQ-2026-000123-IDN"}
    assert {r.eventid for r in result.records} == {"1551200", "1551201"}


def test_parse_gdacs_drops_non_eq():
    result = parse_gdacs(_body("mixed_hazards.json"))
    assert result.ok
    assert result.non_eq_dropped == 1                 # the TC is filtered out
    assert len(result.records) == 1
    assert result.records[0].eventtype == "EQ"


def test_parse_gdacs_skips_one_malformed_feature_without_sinking_feed():
    body = """
    {"type":"FeatureCollection","features":[
      {"type":"Feature","geometry":{"type":"Point","coordinates":[1.0,2.0]},
       "properties":{"eventtype":"EQ","eventid":1,"fromdate":"2026-07-08T00:00:00",
                     "source":"NEIC","sourceid":"us1",
                     "severitydata":{"severity":5.0}}},
      {"type":"Feature","geometry":{"type":"Point","coordinates":[1.0,2.0]},
       "properties":{"eventtype":"EQ","name":"no id, no fromdate"}}
    ]}
    """
    result = parse_gdacs(body)
    assert result.ok                # one bad feature does not sink the feed
    assert result.skipped == 1
    assert len(result.records) == 1
    assert result.records[0].eventid == "1"


def test_parse_gdacs_rejects_garbage():
    assert not parse_gdacs(_body("malformed.json")).ok   # truncated JSON
    assert not parse_gdacs("not json").ok
    assert not parse_gdacs('{"nope": true}').ok           # no features list
