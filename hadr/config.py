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

# --- Slice 3: provisional -> confirmed lifecycle -------------------------------
# A quake is recorded PROVISIONAL on first detection and firms to CONFIRMED when
# an impact signal *settles* in the feed record itself — NO enrichment fetch. The
# confirming signals (any one is sufficient):
#   * USGS review status reaches CONFIRMED_USGS_STATUS ("automatic" -> "reviewed"),
#   * USGS PAGER alert (`properties.alert`, the colour) is non-null,
#   * GDACS is no longer temporary (`istemporary` == "false"; a settled ShakeMap
#     flips it off).
# Confirmation is STICKY: once confirmed an event never regresses to provisional.
STATUS_PROVISIONAL: str = "provisional"
STATUS_CONFIRMED: str = "confirmed"
CONFIRMED_USGS_STATUS: str = "reviewed"

# Material/headline classification. An event is HEADLINE (material) if it is
# CONFIRMED, or its current severity (GDACS `episodealertlevel` or USGS PAGER
# `alert`) is one of these levels; otherwise it is ROUTINE and folds below the
# fold. Compared case-insensitively.
MATERIAL_ALERT_LEVELS: frozenset[str] = frozenset({"orange", "red"})

# A strong quake headlines on magnitude alone, even while provisional and lacking
# an impact signal: a potential major event warrants attention before PAGER/GDACS
# scoring lands (those take ~20-40 min). "Routine" means a *minor* quake near the
# M4.5 floor, not a large one that simply hasn't been reviewed yet — folding an
# M6.8 below the fold because it is minutes-old would bury the thing that matters
# most. This governs DASHBOARD surfacing only; it is NOT an urgent-push trigger
# (that path stays confirmation-only, no magnitude escape hatch — Slice 4).
HEADLINE_MIN_MAGNITUDE: float = 6.0

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
