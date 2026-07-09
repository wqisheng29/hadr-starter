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
