"""Render the ledger to ``dashboard.html``.

``render_dashboard`` is pure (ledger rows + an "as of" instant + feed status ->
an HTML string) so it is testable without touching the filesystem; a thin
``write_dashboard`` handles the side effect. Autoescape is on because USGS
``place``/``title`` are third-party text going into HTML.
"""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .clock import format_sgt, to_sgt
from .model import EventRow, FeedStatus

_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _origin_sgt(iso_utc: str | None) -> str:
    if not iso_utc:
        return ""
    return format_sgt(datetime.fromisoformat(iso_utc))


def render_dashboard(
    events: list[EventRow], as_of_utc: datetime, feed_status: FeedStatus
) -> str:
    template = _env.get_template("dashboard.html.j2")
    rendered = [
        {
            "title": e.title,
            "magnitude": e.magnitude,
            "place": e.place,
            "origin_sgt": _origin_sgt(e.origin_time),
            "origin_utc": e.origin_time or "",
        }
        for e in events
    ]
    return template.render(
        as_of=format_sgt(as_of_utc),
        events=rendered,
        feed_ok=feed_status.is_ok,
        feed_state=feed_status.state.value,
        feed_detail=feed_status.detail,
    )


def write_dashboard(
    events: list[EventRow], as_of_utc: datetime, feed_status: FeedStatus, out_path: str | Path
) -> None:
    Path(out_path).write_text(
        render_dashboard(events, as_of_utc, feed_status), encoding="utf-8"
    )
