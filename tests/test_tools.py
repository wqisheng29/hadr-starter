"""Tool-layer tests: fetch_feed against fixtures, write_dashboard against a
frozen clock, and the registry's failures-are-data dispatch. No network."""

import json

from hadr.fetch import FixtureFeedSource
from hadr.llm import ToolCall
from hadr.model import FetchOutcome
from hadr.tools import (
    Tool,
    ToolRegistry,
    fetch_feed_tool,
    write_dashboard_tool,
)


class _BrokenSource:
    """A FeedSource whose fetch always fails — for the degraded path."""

    def fetch(self) -> FetchOutcome:
        return FetchOutcome(ok=False, error="ConnectError: boom")


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments_json=json.dumps(arguments))


# --- fetch_feed ---------------------------------------------------------------


def test_fetch_feed_returns_normalised_events(fixture_source):
    tool = fetch_feed_tool({"usgs": fixture_source("all_day.json")})
    out = json.loads(tool.handler({"source": "usgs"}))
    assert out["ok"] and out["source"] == "usgs"
    assert out["count"] == len(out["events"]) > 0
    first = out["events"][0]
    assert {"id", "magnitude", "place", "title", "origin_time_utc"} <= first.keys()


def test_fetch_feed_defaults_source_when_omitted(fixture_source):
    tool = fetch_feed_tool({"usgs": fixture_source("all_day.json")})
    assert json.loads(tool.handler({}))["ok"]  # source defaults to usgs


def test_fetch_feed_unknown_source_is_error():
    tool = fetch_feed_tool({"usgs": _BrokenSource()})
    out = json.loads(tool.handler({"source": "gdacs"}))
    assert not out["ok"] and "unknown source" in out["error"]


def test_fetch_feed_unreachable_is_data_not_exception():
    tool = fetch_feed_tool({"usgs": _BrokenSource()})
    out = json.loads(tool.handler({"source": "usgs"}))
    assert not out["ok"] and "boom" in out["error"]


def test_fetch_feed_unparseable_is_data(fixture_source):
    tool = fetch_feed_tool({"usgs": fixture_source("malformed.json")})
    out = json.loads(tool.handler({"source": "usgs"}))
    assert not out["ok"] and out["error"]


# --- write_dashboard ----------------------------------------------------------


def test_write_dashboard_writes_file_and_reports(frozen_clock, tmp_out):
    tool = write_dashboard_tool(tmp_out, frozen_clock)
    out = json.loads(tool.handler({
        "headline": "Morning brief",
        "events": [{"title": "M6.1 near Foo", "magnitude": 6.1, "place": "Foo",
                    "assessment": "Shallow, near a town — likely damage."}],
    }))
    assert out["ok"] and out["count"] == 1
    html = tmp_out.read_text(encoding="utf-8")
    assert "Morning brief" in html and "M6.1" in html and "likely damage" in html


def test_write_dashboard_autoescapes_untrusted_text(frozen_clock, tmp_out):
    tool = write_dashboard_tool(tmp_out, frozen_clock)
    tool.handler({"events": [{"title": "<script>alert(1)</script>",
                              "assessment": "x & y <b>", "place": "P"}]})
    html = tmp_out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html and "x &amp; y" in html


def test_write_dashboard_rejects_non_list_events(frozen_clock, tmp_out):
    tool = write_dashboard_tool(tmp_out, frozen_clock)
    out = json.loads(tool.handler({"events": "nope"}))
    assert not out["ok"] and "must be a list" in out["error"]
    assert not tmp_out.exists()  # nothing written on bad input


def test_write_dashboard_creates_parent_dir(frozen_clock, tmp_path):
    nested = tmp_path / "reports" / "sitrep.html"
    tool = write_dashboard_tool(nested, frozen_clock)
    assert json.loads(tool.handler({"events": []}))["ok"]
    assert nested.exists()


# --- registry dispatch --------------------------------------------------------


def _registry(fixture_source, tmp_out, frozen_clock) -> ToolRegistry:
    return ToolRegistry([
        fetch_feed_tool({"usgs": fixture_source("all_day.json")}),
        write_dashboard_tool(tmp_out, frozen_clock),
    ])


def test_registry_schema_and_names(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    assert reg.names == ("fetch_feed", "write_dashboard")
    names = {t["function"]["name"] for t in reg.schema()}
    assert names == {"fetch_feed", "write_dashboard"}


def test_dispatch_unknown_tool_is_error(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    out = json.loads(reg.dispatch(_call("nope", {})))
    assert not out["ok"] and "unknown tool" in out["error"]


def test_dispatch_invalid_arguments_json_is_error(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    out = json.loads(reg.dispatch(ToolCall(id="c", name="fetch_feed", arguments_json="{not json")))
    assert not out["ok"] and "valid JSON" in out["error"]


def test_dispatch_handler_exception_is_caught():
    def boom(_args: dict) -> str:
        raise ValueError("kaboom")

    reg = ToolRegistry([Tool(name="explode", description="", parameters={}, handler=boom)])
    out = json.loads(reg.dispatch(_call("explode", {})))
    assert not out["ok"] and "ValueError" in out["error"] and "kaboom" in out["error"]
