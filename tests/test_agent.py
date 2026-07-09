"""Agent-loop tests. The model is a scripted fake (no network); the tools are
the real registry over a fixture feed and a frozen clock, so the loop is
exercised end-to-end: model asks -> tool runs -> result re-enters messages."""

import json

from hadr.agent import run_agent
from hadr.llm import ChatResult, ToolCall
from hadr.tools import ToolRegistry, fetch_feed_tool, write_dashboard_tool


class FakeModel:
    """Returns pre-scripted ChatResults in order and records the tools it saw."""

    model = "fake"

    def __init__(self, script: list[ChatResult]) -> None:
        self._script = list(script)
        self.calls = 0

    def complete(self, messages, *, tools=None, max_tokens=2048) -> ChatResult:
        self.calls += 1
        return self._script.pop(0)


def _wants_tools(*calls: tuple[str, str]) -> ChatResult:
    tcs = tuple(
        ToolCall(id=f"c{i}", name=name, arguments_json=arguments)
        for i, (name, arguments) in enumerate(calls)
    )
    raw = [
        {"id": c.id, "type": "function",
         "function": {"name": c.name, "arguments": c.arguments_json}}
        for c in tcs
    ]
    return ChatResult(ok=True, text=None, tool_calls=tcs,
                      message={"role": "assistant", "content": "", "tool_calls": raw})


def _says(text: str) -> ChatResult:
    return ChatResult(ok=True, text=text,
                      message={"role": "assistant", "content": text})


def _registry(fixture_source, tmp_out, frozen_clock) -> ToolRegistry:
    return ToolRegistry([
        fetch_feed_tool({"usgs": fixture_source("all_day.json")}),
        write_dashboard_tool(tmp_out, frozen_clock),
    ])


def test_full_loop_fetch_then_write_then_reply(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    model = FakeModel([
        _wants_tools(("fetch_feed", '{"source": "usgs"}')),
        _wants_tools(("write_dashboard",
                      '{"summary": "One notable quake overnight.", '
                      '"events": [{"title": "M6 Foo", "assessment": "notable"}]}')),
        _says("Wrote the dashboard: one notable quake."),
    ])
    messages = [{"role": "system", "content": "orders"},
                {"role": "user", "content": "brief me"}]

    result = run_agent(model, messages, reg)

    assert result.ok and result.reply.startswith("Wrote the dashboard")
    assert result.steps == 3
    assert tuple(i.name for i in result.invocations) == ("fetch_feed", "write_dashboard")
    # the dashboard tool actually ran and wrote the file, summary included
    html = tmp_out.read_text(encoding="utf-8")
    assert tmp_out.exists() and "M6 Foo" in html
    assert "Executive summary" in html and "One notable quake overnight" in html
    # tool results were fed back into the thread as role:"tool" turns
    tool_turns = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_turns) == 2
    assert json.loads(tool_turns[0]["content"])["ok"]  # fetch_feed result re-entered


def test_loop_stops_on_plain_reply_without_calling_tools(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    model = FakeModel([_says("No tools needed.")])
    result = run_agent(model, [{"role": "user", "content": "hi"}], reg)
    assert result.ok and result.reply == "No tools needed."
    assert result.invocations == () and result.steps == 1


def test_model_error_propagates(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    model = FakeModel([ChatResult(ok=False, error="HTTP 401: unauthorized")])
    result = run_agent(model, [{"role": "user", "content": "hi"}], reg)
    assert not result.ok and "401" in result.error


def test_max_steps_guard_trips_on_endless_tool_calls(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    # Always asks for a tool, never a final answer.
    model = FakeModel([_wants_tools(("fetch_feed", "{}")) for _ in range(10)])
    result = run_agent(model, [{"role": "user", "content": "loop"}], reg, max_steps=3)
    assert not result.ok and "stopped after 3 steps" in result.error
    assert result.steps == 3 and model.calls == 3


def test_on_tool_callback_fires_per_invocation(fixture_source, tmp_out, frozen_clock):
    reg = _registry(fixture_source, tmp_out, frozen_clock)
    model = FakeModel([
        _wants_tools(("fetch_feed", '{"source": "usgs"}')),
        _says("done"),
    ])
    seen = []
    run_agent(model, [{"role": "user", "content": "go"}], reg, on_tool=seen.append)
    assert [i.name for i in seen] == ["fetch_feed"]
