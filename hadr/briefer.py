"""Render the ledger to ``dashboard.html``.

``render_dashboard`` is pure (ledger rows + an "as of" instant + feed status ->
an HTML string) so it is testable without touching the filesystem; a thin
``write_dashboard`` handles the side effect. Autoescape is on because USGS
``place``/``title`` are third-party text going into HTML.
"""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from . import config
from .clock import format_sgt, to_sgt
from .model import EventRow, FeedStatus

_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    # Escape unconditionally: every template here renders untrusted third-party
    # feed text into HTML. select_autoescape() would silently NOT escape, because
    # its default extension list matches ".html" but not this project's compound
    # ".html.j2" template names.
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _origin_sgt(iso_utc: str | None) -> str:
    if not iso_utc:
        return ""
    return format_sgt(datetime.fromisoformat(iso_utc))


# GDACS alert colours, for the (conditionally rendered) severity tag.
_ALERT_COLOUR = {"green": "#2e7d32", "orange": "#e65100", "red": "#b30000"}


def _is_material(
    status: str | None,
    gdacs_alert: str | None,
    pager_alert: str | None,
    magnitude: float | None,
) -> bool:
    """Headline (material) if CONFIRMED, or current severity (GDACS
    episodealertlevel / PAGER colour) is a material level, or the magnitude is
    strong enough to matter before any impact signal has landed — else routine.
    All thresholds are config-driven (``MATERIAL_ALERT_LEVELS``,
    ``HEADLINE_MIN_MAGNITUDE``), never in prose."""
    if status == config.STATUS_CONFIRMED:
        return True
    for level in (gdacs_alert, pager_alert):
        if level and level.lower() in config.MATERIAL_ALERT_LEVELS:
            return True
    if magnitude is not None and magnitude >= config.HEADLINE_MIN_MAGNITUDE:
        return True
    return False


def _view(e: EventRow) -> dict:
    """Flatten one EventRow into the template's view model, with the lifecycle
    status resolved (a missing status reads as provisional) and materiality
    classified up front so the template just iterates two lists."""
    status = e.status or config.STATUS_PROVISIONAL
    return {
        "title": e.title,
        "magnitude": e.magnitude,
        "place": e.place,
        "origin_sgt": _origin_sgt(e.origin_time),
        "origin_utc": e.origin_time or "",
        "sources": e.sources,
        "sources_label": " · ".join(s.upper() for s in e.sources),
        "gdacs_alert": e.gdacs_episodealertlevel,
        "gdacs_alert_colour": _ALERT_COLOUR.get(
            (e.gdacs_episodealertlevel or "").lower(), "#5b5b57"
        ),
        "status": status,
        "is_confirmed": status == config.STATUS_CONFIRMED,
        "is_material": _is_material(
            status, e.gdacs_episodealertlevel, e.pager_alert, e.magnitude
        ),
    }


def render_dashboard(
    events: list[EventRow],
    as_of_utc: datetime,
    feed_status: FeedStatus,
    feed_statuses: list[FeedStatus] | None = None,
) -> str:
    """Render the dashboard. ``feed_statuses`` lists every feed the run touched
    (defaulting to just ``feed_status`` for slice-1 callers); one banner is
    emitted per non-ok feed. Each banner reads ``<SOURCE> feed ...`` (``usgs`` ->
    ``USGS``).

    Material/confirmed events are HEADLINED in the main table (with a
    provisional/confirmed marker); routine ones fold into a collapsed summary
    below the fold rather than being featured (Slice 3). Order within each group
    follows ``events`` (magnitude desc)."""
    statuses = list(feed_statuses) if feed_statuses is not None else [feed_status]
    template = _env.get_template("dashboard.html.j2")
    views = [_view(e) for e in events]
    headline = [v for v in views if v["is_material"]]
    routine = [v for v in views if not v["is_material"]]
    banners = [
        {"label": s.source.upper(), "state": s.state.value, "detail": s.detail}
        for s in statuses
        if not s.is_ok
    ]
    return template.render(
        as_of=format_sgt(as_of_utc),
        events=views,
        headline=headline,
        routine=routine,
        feed_banners=banners,
    )


def write_dashboard(
    events: list[EventRow],
    as_of_utc: datetime,
    feed_status: FeedStatus,
    out_path: str | Path,
    feed_statuses: list[FeedStatus] | None = None,
) -> None:
    Path(out_path).write_text(
        render_dashboard(events, as_of_utc, feed_status, feed_statuses),
        encoding="utf-8",
    )
