"""Fetch-boundary tests: HTTP error mapping (via MockTransport, no network) and
the pure parser."""

import httpx

from hadr.fetch import HttpFeedSource, parse_usgs


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_http_200_ok():
    def handler(request):
        return httpx.Response(200, text='{"features": []}')

    outcome = HttpFeedSource("https://example/feed", _client(handler)).fetch()
    assert outcome.ok and outcome.body == '{"features": []}'


def test_http_non_200_maps_to_not_ok():
    def handler(request):
        return httpx.Response(503, text="down")

    outcome = HttpFeedSource("https://example/feed", _client(handler)).fetch()
    assert not outcome.ok and outcome.status == 503


def test_http_transport_error_maps_to_not_ok():
    def handler(request):
        raise httpx.ConnectError("boom")

    outcome = HttpFeedSource("https://example/feed", _client(handler)).fetch()
    assert not outcome.ok and "ConnectError" in outcome.error


def test_parse_usgs_ids_and_null_mag():
    body = """
    {"type":"FeatureCollection","features":[
      {"type":"Feature","id":"us1",
       "properties":{"mag":6.1,"place":"p","time":1,"updated":2,"ids":",ci9,us1,","title":"T"},
       "geometry":{"type":"Point","coordinates":[1.0,2.0,3.0]}},
      {"type":"Feature","id":"us2",
       "properties":{"mag":null,"place":"q","time":5,"updated":6,"ids":",us2,","title":"U"},
       "geometry":{"type":"Point","coordinates":[4.0,5.0,6.0]}}
    ]}
    """
    result = parse_usgs(body)
    assert result.ok
    first, second = result.records
    assert first.ids == frozenset({"ci9", "us1"})  # comma padding stripped
    assert first.magnitude == 6.1
    assert second.magnitude is None


def test_parse_usgs_rejects_garbage():
    assert not parse_usgs("not json").ok
    assert not parse_usgs('{"nope": true}').ok  # no features list
