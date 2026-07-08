"""Tools the agent can call.

Each tool is a name, an OpenAI function spec (so the model knows how to call it),
and a Python handler that turns parsed arguments into a result string. Tools wrap
*injected* dependencies (a ``FeedSource``, the ledger) so the agent layer stays
testable with fakes — same discipline as the deterministic core's ``FeedSource``.

Level 3 — ``fetch_feed``: fetch a HADR feed and return its raw body.
Level 5 — ``write_dashboard``: save an HTML page of the model's assessed events.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from jinja2 import Environment, PackageLoader

from . import config
from .clock import format_sgt, to_sgt
from .fetch import FeedSource
from .model import FetchOutcome


_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    autoescape=True,  # all our templates are HTML; feed text + model prose flow in.
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class Tool:
    name: str
    spec: dict                       # the OpenAI "function" descriptor
    fn: Callable[[dict], str]        # parsed arguments -> result string


class ToolRegistry:
    """Name -> Tool, with the specs ready to hand the model."""

    def __init__(self, tools: list[Tool]) -> None:
        self._by_name = {t.name: t for t in tools}

    @property
    def specs(self) -> list[dict]:
        return [{"type": "function", "function": t.spec} for t in self._by_name.values()]

    def has(self, name: str) -> bool:
        return name in self._by_name

    def call(self, name: str, arguments: dict) -> str:
        return self._by_name[name].fn(arguments)


_FETCH_FEED_SPEC = {
    "name": "fetch_feed",
    "description": (
        "Fetch a HADR disaster feed and return its raw body (USGS GeoJSON). "
        "Use this to see recent earthquakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "feed": {
                "type": "string",
                "enum": ["usgs"],
                "description": "Which feed to fetch.",
            }
        },
        "required": ["feed"],
    },
}


def make_fetch_feed(sources: dict[str, FeedSource]) -> Callable[[dict], str]:
    """Build a fetch_feed handler bound to injected feed sources.

    ``sources`` maps a feed name (``"usgs"``) to a ``FeedSource`` — an
    ``HttpFeedSource`` in production, a ``FixtureFeedSource`` in tests. This is
    the seam: the tool never touches the network directly.
    """

    def _fetch_feed(args: dict) -> str:
        feed = args.get("feed", "")
        source = sources.get(feed)
        if source is None:
            return f"error: unknown feed {feed!r}; known: {sorted(sources)}"
        outcome: FetchOutcome = source.fetch()
        if not outcome.ok:
            detail = outcome.error or f"HTTP {outcome.status}"
            return f"error: feed {feed!r} unreachable ({detail})"
        return outcome.body or ""

    return _fetch_feed


_WRITE_DASHBOARD_SPEC = {
    "name": "write_dashboard",
    "description": (
        "Save an HTML dashboard of the events you have assessed. Call this after "
        "fetch_feed and forming your impact assessments. Each event carries a "
        "title, magnitude, place, and your assessment prose."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "description": "The assessed events to publish.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title, e.g. 'M 6.8 - 120 km SW of Padang'."},
                        "magnitude": {"type": "number", "description": "Magnitude (e.g. 6.8)."},
                        "place": {"type": "string", "description": "Human-readable place."},
                        "assessment": {"type": "string", "description": "Your impact assessment: who/what is affected, severity."},
                    },
                    "required": ["title", "assessment"],
                },
            },
            "summary": {"type": "string", "description": "One-line situation summary for the header."},
        },
        "required": ["events"],
    },
}


def _render_agent_dashboard(events: list[dict], summary: str | None, now: datetime) -> str:
    template = _env.get_template("agent_dashboard.html.j2")
    return template.render(
        as_of=format_sgt(to_sgt(now)),
        summary=summary or "",
        events=events,
    )


def make_write_dashboard(
    out_path: str | Path = config.DEFAULT_OUT_PATH,
    now: datetime | None = None,
) -> Callable[[dict], str]:
    """Build a write_dashboard handler bound to an output path.

    ``out_path`` is injected (tests pass ``tmp_path``) so the tool never touches
    a fixed location implicitly. ``now`` fixes the "as of" timestamp; production
    leaves it ``None`` for wall-clock time, tests inject a frozen instant.
    """
    out = Path(out_path)

    def _write_dashboard(args: dict) -> str:
        events = args.get("events") or []
        if not isinstance(events, list):
            return "error: 'events' must be a list"
        summary = args.get("summary")
        instant = now or datetime.now(timezone.utc)
        try:
            html = _render_agent_dashboard(events, summary, instant)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
        except (OSError, ValueError, TypeError) as exc:
            return f"error: could not write dashboard ({exc})"
        return f"wrote {out} with {len(events)} assessed event(s)"

    return _write_dashboard


def make_default_tools(
    sources: dict[str, FeedSource],
    dashboard_path: str | Path = config.DEFAULT_OUT_PATH,
    now: datetime | None = None,
) -> ToolRegistry:
    return ToolRegistry([
        Tool("fetch_feed", _FETCH_FEED_SPEC, make_fetch_feed(sources)),
        Tool("write_dashboard", _WRITE_DASHBOARD_SPEC, make_write_dashboard(dashboard_path, now)),
    ])


def run_tool_calls(tool_calls: list[dict], registry: ToolRegistry, messages: list[dict]) -> None:
    """Execute each tool call the model made, appending ``tool`` result messages.

    Unknown tools and bad JSON degrade to an error string in the result, never an
    exception — the same "failures are data" posture as the feed layer.
    """
    for call in tool_calls:
        call_id = call.get("id", "")
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as exc:
            result = f"error: bad tool arguments JSON ({exc})"
        else:
            if not registry.has(name):
                result = f"error: unknown tool {name!r}"
            else:
                result = registry.call(name, args)
        messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
