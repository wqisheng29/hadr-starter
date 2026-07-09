"""The agent's tools: ``fetch_feed`` and ``write_dashboard``.

A tool is two things bolted together: a JSON-Schema *description* the model sees
(so it can decide to call it) and a *handler* your code runs when it does. The
handler returns a **string**, which goes back into the messages array as the
tool's result — including error strings. A failed tool is data the model can
react to (retry, pick another source, apologise in the brief), not an exception
that kills the agent loop. That is the same "failures are data" posture the feed
boundary takes; here it also keeps a misbehaving model from crashing the run.

Both tools take their real-world dependencies by injection — ``fetch_feed`` a
mapping of feed name to ``FeedSource``, ``write_dashboard`` an output path and a
``Clock`` — so the whole tool layer is exercised against fixtures and a frozen
clock with no network and no wall-clock reads.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from .clock import Clock, format_sgt
from .fetch import FeedSource, parse_usgs
from .llm import ToolCall

_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    # Escape unconditionally: both feed text and model-authored assessments are
    # untrusted here. (select_autoescape() misses this project's ".html.j2"
    # template names — see the note in briefer.py.)
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class Tool:
    """A model-callable function: its advertised schema plus the code to run it."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments object
    handler: Callable[[dict], str]

    def schema(self) -> dict:
        """The OpenAI-style ``tools`` entry the model is shown."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Holds the tools, advertises their schemas, and dispatches calls by name.

    ``dispatch`` never raises: an unknown tool, un-parseable arguments, or a
    handler that blows up all come back as an ``{"ok": false, "error": ...}``
    string for the model to read.
    """

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schema(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def dispatch(self, call: ToolCall) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return _err(f"unknown tool '{call.name}'; available: {list(self._tools)}")
        try:
            arguments = json.loads(call.arguments_json) if call.arguments_json else {}
        except json.JSONDecodeError as exc:
            return _err(f"arguments were not valid JSON: {exc}")
        if not isinstance(arguments, dict):
            return _err("arguments must be a JSON object")
        try:
            return tool.handler(arguments)
        except Exception as exc:  # noqa: BLE001 - a buggy tool is data, not a crash
            return _err(f"{tool.name} raised {type(exc).__name__}: {exc}")


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


# --- fetch_feed ---------------------------------------------------------------


def fetch_feed_tool(sources: dict[str, FeedSource]) -> Tool:
    """A tool that fetches a HADR feed and returns normalised events as JSON.

    ``sources`` maps a feed name (e.g. ``"usgs"``) to an injected ``FeedSource``
    — a fixture in tests, live HTTP in production. The raw feed body is parsed
    into compact records so the model reasons over a small, uniform list instead
    of a page of GeoJSON.
    """

    def handler(args: dict) -> str:
        name = args.get("source", "usgs")
        source = sources.get(name)
        if source is None:
            return _err(f"unknown source '{name}'; available: {sorted(sources)}")

        outcome = source.fetch()
        if not outcome.ok:
            detail = outcome.error or f"HTTP {outcome.status}"
            return json.dumps({"ok": False, "source": name, "error": detail})

        parsed = parse_usgs(outcome.body or "")
        if not parsed.ok:
            return json.dumps({"ok": False, "source": name, "error": parsed.error})

        events = [
            {
                "id": r.preferred_id,
                "magnitude": r.magnitude,
                "place": r.place,
                "title": r.title,
                "origin_time_utc": r.origin_time_utc.isoformat(),
            }
            for r in parsed.records
        ]
        return json.dumps(
            {"ok": True, "source": name, "count": len(events), "events": events}
        )

    return Tool(
        name="fetch_feed",
        description=(
            "Fetch a humanitarian disaster feed and return its current events as "
            "normalised JSON (id, magnitude, place, title, origin_time_utc). Call "
            "this before assessing or writing a dashboard."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": sorted(sources),
                    "description": "which feed to fetch",
                }
            },
            "required": [],
        },
        handler=handler,
    )


# --- write_dashboard ----------------------------------------------------------


def render_sitrep(headline: str, events: list[dict], as_of_utc: datetime) -> str:
    """Pure render of assessed events to an HTML string (autoescaped)."""
    rows = [
        {
            "title": e.get("title") or "—",
            "magnitude": e.get("magnitude"),
            "place": e.get("place") or "",
            "assessment": e.get("assessment") or "",
        }
        for e in events
    ]
    return _env.get_template("agent_sitrep.html.j2").render(
        headline=headline, as_of=format_sgt(as_of_utc), events=rows
    )


def write_dashboard_tool(out_path: str | Path, clock: Clock) -> Tool:
    """A tool that saves the model's assessed events as an HTML dashboard.

    The clock is injected (not ``datetime.now()``) so the rendered "as of" line
    is deterministic under a ``FrozenClock`` in tests, per the house rules.
    """

    def handler(args: dict) -> str:
        events = args.get("events", [])
        if not isinstance(events, list):
            return _err("'events' must be a list of assessed events")
        headline = args.get("headline") or "HADR situation report"
        html = render_sitrep(headline, events, clock.now())
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return json.dumps({"ok": True, "path": str(path), "count": len(events)})

    return Tool(
        name="write_dashboard",
        description=(
            "Save an HTML dashboard of assessed events to disk. Provide the "
            "events you have assessed, each with a short impact assessment. Call "
            "this once you have gathered and judged the events."
        ),
        parameters={
            "type": "object",
            "properties": {
                "headline": {
                    "type": "string",
                    "description": "short title for the report",
                },
                "events": {
                    "type": "array",
                    "description": "the events to show, in priority order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "magnitude": {"type": ["number", "null"]},
                            "place": {"type": "string"},
                            "assessment": {
                                "type": "string",
                                "description": "your impact assessment of this event",
                            },
                        },
                        "required": ["title", "assessment"],
                    },
                },
            },
            "required": ["events"],
        },
        handler=handler,
    )
