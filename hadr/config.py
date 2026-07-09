"""Static configuration for the deterministic core.

Slice 1 only needs the USGS feed and the materiality floor. Thresholds live here
(not in prompts) so behaviour is reproducible and reviewable.
"""

from pathlib import Path

# Materiality floor. USGS global completeness is only ~M4.5-5.0, so below this
# floor *absence* is unreliable; a lower floor would persist noise without
# buying coverage. Quakes strictly below this magnitude are dropped, not stored.
MIN_MAGNITUDE: float = 4.5

# USGS "all earthquakes, past day" summary feed (verified in feeds/usgs.md).
USGS_URL: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"

# GDACS EVENTS4APP GeoJSON event list (verified in feeds/gdacs.md).
GDACS_URL: str = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"

# GDACS `source` values that denote a US/NEIC-detected quake — i.e. the same
# physical event USGS already carries, so its `sourceid` is a USGS `id`. Compared
# case-insensitively; a match routes the record onto the USGS canonical event.
NEIC_SOURCES: frozenset[str] = frozenset({"neic", "us", "usgs"})

# GDACS alert-level ranking (green < orange < red). Used to keep the event-level
# `gdacs_alertlevel` monotonic ("was it ever this severe"), distinct from the
# latest-episode `gdacs_episodealertlevel`. Compared case-insensitively.
GDACS_ALERT_RANK: dict[str, int] = {"green": 0, "orange": 1, "red": 2}

# Default artifact locations, relative to the current working directory.
DEFAULT_DB_PATH: Path = Path("state/ledger.db")
DEFAULT_OUT_PATH: Path = Path("dashboard.html")

# Singapore Standard Time is a fixed UTC+8 (no DST). Used for the "as of" header.
SGT_TZ_NAME: str = "Asia/Singapore"

# LLM provider: OpenCode Go, an OpenAI-compatible gateway (docs.opencode.ai/go).
# The API key is read from the OPENCODE_API_KEY env var (never stored here);
# these two are overridable via OPENCODE_BASE_URL / OPENCODE_MODEL. Go serves
# curated open-source coding models (GLM, Kimi, DeepSeek, Qwen, ...) — the
# frontier models live on the separate pay-as-you-go Zen tier.
OPENCODE_BASE_URL: str = "https://opencode.ai/zen/go/v1"
OPENCODE_MODEL: str = "glm-5.2"

# HTTP read/connect timeout for a single model call, in seconds. Generous on
# purpose: glm-5.2 is a reasoning model and an agent turn (reason over the feed,
# then emit a write_dashboard tool call) routinely runs past a minute. This is a
# batch agent, not a latency-sensitive request. Override with OPENCODE_TIMEOUT.
OPENCODE_TIMEOUT_S: float = 180.0
