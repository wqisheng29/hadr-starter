"""Harness tests, driven at the model seam.

The chat loop is pure with respect to the network: a fake ``ChatModel`` plus
``StringIO`` streams make a full conversation deterministic — the same posture
the pipeline tests take toward ``FeedSource``.
"""

import io
import json

import httpx

from hadr.agent import chat_loop


class _EchoModel:
    """Replies ``echo:<last user message>`` — deterministic and inspectable."""

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        return {"role": "assistant", "content": f"echo:{messages[-1]['content']}"}


class _FailingModel:
    """Always blows up — exercises graceful degradation."""

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        raise httpx.ConnectError("boom")


def test_chat_loop_appends_turns_and_prints_replies():
    reader = io.StringIO("hello\nworld\n")
    writer = io.StringIO()

    messages = chat_loop(_EchoModel(), reader, writer)

    assert messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "echo:hello"},
        {"role": "user", "content": "world"},
        {"role": "assistant", "content": "echo:world"},
    ]
    out = writer.getvalue()
    assert "echo:hello" in out
    assert "echo:world" in out


def test_chat_loop_sends_the_whole_history_each_turn():
    seen: list[list[dict]] = []

    class _Recording:
        def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
            seen.append([m for m in messages])
            return {"role": "assistant", "content": "ok"}

    chat_loop(_Recording(), io.StringIO("a\nb\n"), io.StringIO())

    # Second turn must include the first user+assistant pair, not just "b".
    assert seen[0] == [{"role": "user", "content": "a"}]
    assert seen[1] == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "b"},
    ]


def test_chat_loop_survives_a_failed_turn():
    reader = io.StringIO("hi\n")
    writer = io.StringIO()

    messages = chat_loop(_FailingModel(), reader, writer)

    assert "(error:" in writer.getvalue()
    # The user turn stays in history even though the turn failed.
    assert messages == [{"role": "user", "content": "hi"}]


def test_chat_loop_stops_cleanly_on_eof():
    writer = io.StringIO()
    messages = chat_loop(_EchoModel(), io.StringIO(""), writer)
    assert messages == []


def test_chat_loop_prepends_system_prompt_every_turn():
    seen: list[list[dict]] = []

    class _Recording:
        def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
            seen.append([m for m in messages])
            return {"role": "assistant", "content": "ok"}

    system = "You are a HADR briefer. Be terse."
    chat_loop(_Recording(), io.StringIO("a\nb\n"), io.StringIO(), system=system)

    # The system message is the head of history on every turn, never dropped.
    assert seen[0] == [
        {"role": "system", "content": system},
        {"role": "user", "content": "a"},
    ]
    assert seen[1][0] == {"role": "system", "content": system}
    assert seen[1] == [
        {"role": "system", "content": system},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "b"},
    ]


def test_chat_loop_without_system_matches_level1():
    # system=None must behave exactly as before — no phantom system message.
    messages = chat_loop(_EchoModel(), io.StringIO("hi\n"), io.StringIO())
    assert messages[0] == {"role": "user", "content": "hi"}


def test_load_standing_orders_reads_file(tmp_path):
    from pathlib import Path

    from hadr.agent import load_standing_orders

    f = tmp_path / "orders.md"
    f.write_text("# Orders\nBe terse.", encoding="utf-8")
    assert load_standing_orders(f) == "# Orders\nBe terse."


def test_load_standing_orders_missing_returns_none(tmp_path):
    from hadr.agent import load_standing_orders

    assert load_standing_orders(tmp_path / "nope.md") is None


# --- Level 3: one tool (fetch_feed) -----------------------------------------

from pathlib import Path

from hadr.fetch import FixtureFeedSource
from hadr.model import FetchOutcome
from hadr.tools import make_default_tools, make_fetch_feed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "usgs"


class _ScriptedModel:
    """Returns canned assistant messages in order — for tool-call flows."""

    def __init__(self, replies: list[dict]) -> None:
        self._replies = list(replies)
        self._i = 0
        self.seen_tools: list[list[dict] | None] = []

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        self.seen_tools.append(tools)
        r = self._replies[self._i]
        self._i += 1
        return r


def _usgs_tool(fixture: str = "all_day.json", dashboard_path=None, now=None):
    from pathlib import Path
    kwargs = {"sources": {"usgs": FixtureFeedSource(FIXTURES / fixture)}}
    if dashboard_path is not None:
        kwargs["dashboard_path"] = dashboard_path
    if now is not None:
        kwargs["now"] = now
    return make_default_tools(**kwargs)


def test_chat_loop_runs_one_tool_call_and_prints_followup():
    tool_calls = [{
        "id": "call_1", "type": "function",
        "function": {"name": "fetch_feed", "arguments": '{"feed": "usgs"}'},
    }]
    model = _ScriptedModel([
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "assistant", "content": "I fetched the USGS feed."},
    ])
    tools = _usgs_tool()
    writer = io.StringIO()

    messages = chat_loop(model, io.StringIO("show me quakes\n"), writer, tools=tools)

    # The tool result is in history as a tool message, carrying the GeoJSON body.
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert "features" in tool_msgs[0]["content"]
    # The follow-up reply was printed.
    assert "I fetched the USGS feed." in writer.getvalue()


def test_chat_loop_passes_tool_specs_to_model():
    model = _ScriptedModel([{"role": "assistant", "content": "no tools needed"}])
    tools = _usgs_tool()
    chat_loop(model, io.StringIO("hi\n"), io.StringIO(), tools=tools)
    assert model.seen_tools[0] is not None
    assert model.seen_tools[0][0]["type"] == "function"
    assert model.seen_tools[0][0]["function"]["name"] == "fetch_feed"


def test_chat_loop_without_tools_passes_none():
    model = _ScriptedModel([{"role": "assistant", "content": "ok"}])
    chat_loop(model, io.StringIO("hi\n"), io.StringIO())
    assert model.seen_tools[0] is None


def test_fetch_feed_returns_body_for_known_feed():
    fn = make_fetch_feed({"usgs": FixtureFeedSource(FIXTURES / "all_day.json")})
    body = fn({"feed": "usgs"})
    assert "features" in body


def test_fetch_feed_unknown_feed_returns_error():
    fn = make_fetch_feed({})
    assert "error" in fn({"feed": "nope"})


def test_fetch_feed_unreachable_returns_error():
    class _Bad:
        def fetch(self) -> FetchOutcome:
            return FetchOutcome(ok=False, error="timeout")
    fn = make_fetch_feed({"usgs": _Bad()})
    assert "error" in fn({"feed": "usgs"})


def test_run_tool_calls_unknown_tool_degrades_to_error_string():
    from hadr.tools import ToolRegistry, run_tool_calls
    registry = ToolRegistry([])  # no tools registered
    messages: list[dict] = []
    run_tool_calls(
        [{"id": "x", "type": "function",
          "function": {"name": "nope", "arguments": "{}"}}],
        registry, messages,
    )
    assert messages[0]["role"] == "tool"
    assert "error" in messages[0]["content"]


# --- Level 4: the agent loop ------------------------------------------------


def _tc(i: int) -> dict:
    return {"id": f"call_{i}", "type": "function",
            "function": {"name": "fetch_feed", "arguments": '{"feed": "usgs"}'}}


def test_agent_loop_runs_multiple_rounds_then_stops_on_plain_reply():
    model = _ScriptedModel([
        {"role": "assistant", "content": None, "tool_calls": [_tc(1)]},
        {"role": "assistant", "content": None, "tool_calls": [_tc(2)]},
        {"role": "assistant", "content": None, "tool_calls": [_tc(3)]},
        {"role": "assistant", "content": "Done assessing 3 fetches."},
    ])
    tools = _usgs_tool()
    writer = io.StringIO()

    messages = chat_loop(model, io.StringIO("assess\n"), writer, tools=tools)

    # Three tool results, one final plain reply, loop stopped cleanly.
    assert len([m for m in messages if m.get("role") == "tool"]) == 3
    assert messages[-1] == {"role": "assistant", "content": "Done assessing 3 fetches."}
    assert "stopped" not in writer.getvalue()


def test_agent_loop_stops_immediately_when_no_tool_calls():
    model = _ScriptedModel([{"role": "assistant", "content": "I don't need tools."}])
    tools = _usgs_tool()
    writer = io.StringIO()

    messages = chat_loop(model, io.StringIO("hi\n"), writer, tools=tools)

    assert [m for m in messages if m.get("role") == "tool"] == []
    assert messages[-1]["content"] == "I don't need tools."


def test_agent_loop_iteration_cap_stops_runaway_tool_requests():
    # Model never stops requesting tools — the cap must halt it.
    def _always_tool():
        while True:
            yield {"role": "assistant", "content": None, "tool_calls": [_tc(99)]}

    class _Greedy:
        def __init__(self):
            self._gen = _always_tool()
            self.calls = 0
        def complete(self, messages, tools=None):
            self.calls += 1
            return next(self._gen)

    model = _Greedy()
    tools = _usgs_tool()
    writer = io.StringIO()

    chat_loop(model, io.StringIO("go\n"), writer, tools=tools, max_iterations=3)

    out = writer.getvalue()
    assert "stopped" in out
    assert "3" in out
    # Cap is on tool-rounds: 1 initial + 2 re-asks (after rounds 1,2) = 3 calls;
    # the 3rd round hits the cap and breaks without re-asking.
    assert model.calls == 3


# --- Level 5: second tool (write_dashboard) ---------------------------------

from datetime import datetime, timezone

from hadr.tools import make_write_dashboard


_FROZEN = datetime(2026, 7, 8, 0, 30, 0, tzinfo=timezone.utc)  # 08:30 SGT


def _write_tc(events: list[dict], summary: str | None = None) -> dict:
    args = {"events": events}
    if summary is not None:
        args["summary"] = summary
    return {"id": "w1", "type": "function",
            "function": {"name": "write_dashboard", "arguments": json.dumps(args)}}


def test_write_dashboard_saves_html_with_assessed_events(tmp_path):
    out = tmp_path / "dashboard.html"
    fn = make_write_dashboard(out, now=_FROZEN)
    events = [
        {"title": "M 6.8 - 120 km SW of Padang", "magnitude": 6.8,
         "place": "120 km SW of Padang, Indonesia",
         "assessment": "Significant quake near populated coast; tsunami risk low."},
    ]
    result = fn({"events": events, "summary": "One significant event monitored."})
    assert "wrote" in result and "1" in result
    html = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "M 6.8 - 120 km SW of Padang" in html
    assert "Significant quake near populated coast" in html
    assert "One significant event monitored." in html
    assert "as of 2026-07-08 08:30 SGT" in html


def test_write_dashboard_autoescapes_model_prose(tmp_path):
    out = tmp_path / "d.html"
    fn = make_write_dashboard(out, now=_FROZEN)
    # A payload that would break out of HTML if unescaped.
    evil = "<script>alert(1)</script>"
    fn({"events": [{"title": evil, "assessment": evil}]})
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html  # escaped
    assert "&lt;script&gt;" in html


def test_write_dashboard_empty_events_renders_placeholder(tmp_path):
    out = tmp_path / "d.html"
    fn = make_write_dashboard(out, now=_FROZEN)
    fn({"events": []})
    assert "No assessed events." in out.read_text(encoding="utf-8")


def test_write_dashboard_bad_events_arg_returns_error(tmp_path):
    fn = make_write_dashboard(tmp_path / "d.html", now=_FROZEN)
    assert "error" in fn({"events": "not a list"})


def test_agent_full_flow_fetch_then_write(tmp_path):
    """The end-state shape: fetch_feed -> assess -> write_dashboard, in one turn."""
    out = tmp_path / "dashboard.html"
    tools = _usgs_tool(dashboard_path=out, now=_FROZEN)
    fetch_tc = {"id": "f1", "type": "function",
                "function": {"name": "fetch_feed", "arguments": '{"feed": "usgs"}'}}
    model = _ScriptedModel([
        {"role": "assistant", "content": None, "tool_calls": [fetch_tc]},
        {"role": "assistant", "content": None, "tool_calls": [
            _write_tc([{"title": "M 6.8 - Padang", "magnitude": 6.8,
                        "place": "Padang", "assessment": "Major event."}],
                      summary="One major event.")],
        },
        {"role": "assistant", "content": "Dashboard saved."},
    ])
    writer = io.StringIO()

    messages = chat_loop(model, io.StringIO("assess and brief\n"), writer, tools=tools)

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2  # fetch_feed result, then write_dashboard result
    assert "wrote" in tool_msgs[1]["content"]
    html = out.read_text(encoding="utf-8")
    assert "M 6.8 - Padang" in html
    assert "Major event." in html
    assert "Dashboard saved." in writer.getvalue()
