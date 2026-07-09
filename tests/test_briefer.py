"""Briefer render tests. The load-bearing one guards autoescape: USGS
``place``/``title`` are third-party text rendered into the committed
dashboard.html, so a regression to select_autoescape() (which does NOT match
this project's ``.html.j2`` template names) would reopen a stored-XSS hole."""

from datetime import datetime, timezone

from hadr.briefer import render_dashboard
from hadr.model import EventRow, FeedStatus

_AS_OF = datetime(2026, 7, 8, 0, 30, 0, tzinfo=timezone.utc)


def test_dashboard_autoescapes_untrusted_feed_text():
    row = EventRow(
        canonical_id="usgs:x",
        title="<script>alert(1)</script>",
        magnitude=6.0,
        place="Nowhere & <b>evil</b>",
        origin_time="2026-07-08T00:00:00+00:00",
    )
    html = render_dashboard([row], _AS_OF, FeedStatus.ok("usgs"))

    assert "<script>alert(1)</script>" not in html  # not rendered as live markup
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Nowhere &amp; &lt;b&gt;evil&lt;/b&gt;" in html


def test_dashboard_renders_empty_state():
    html = render_dashboard([], _AS_OF, FeedStatus.ok("usgs"))
    assert "No earthquakes" in html


def test_empty_dashboard_has_no_summary():
    html = render_dashboard([], _AS_OF, FeedStatus.ok("usgs"))
    assert "Executive summary" not in html


def test_dashboard_summary_counts_and_headline():
    events = [
        EventRow(canonical_id="usgs:a", title="M6.8", magnitude=6.8,
                 place="120 km SW of Padang, Indonesia",
                 origin_time="2026-07-06T01:06:40+00:00"),
        EventRow(canonical_id="usgs:b", title="M6.0", magnitude=6.0,
                 place="Somewhere", origin_time="2026-07-06T02:00:00+00:00"),
        EventRow(canonical_id="usgs:c", title="M4.5", magnitude=4.5,
                 place="Minor place", origin_time="2026-07-06T03:00:00+00:00"),
    ]
    html = render_dashboard(events, _AS_OF, FeedStatus.ok("usgs"))

    # The summary leads the brief, counts each bucket, and names the headline event.
    assert "Executive summary" in html
    assert "2 material earthquakes" in html
    assert "led by M6.8 — 120 km SW of Padang, Indonesia" in html
    assert "1 routine or ongoing event below the fold" in html
