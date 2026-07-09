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
# USGS publishes withdrawn quakes with this review status; the pipeline reads it
# as a POSITIVE source withdrawal and marks the canonical event retracted (Slice
# 5), never a normal field update (it must not resurrect the old magnitude).
DELETED_USGS_STATUS: str = "deleted"

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

# --- Slice 5: retraction / aged-out lifecycle + the "since last brief" diff ----
# Two terminal lifecycle statuses beyond provisional/confirmed. RETRACTED = the
# source POSITIVELY withdrew the event (a USGS deletion, or a feed record removed
# while still in window); AGED_OUT = it merely left a feed's revision window (a
# normal scroll-out, "not confirmed ended"). The two are never conflated — see
# the disappearance-detection heuristic in ledger.reconcile_absences. Both are
# sticky/terminal: absence detection never re-classifies an already-terminal row.
STATUS_RETRACTED: str = "retracted"
STATUS_AGED_OUT: str = "aged_out"

# Feed revision windows, used only to tell aged-out from retraction on a
# disappearance. USGS revises quakes for ~72h; GDACS carries an event for ~4
# days. An event absent from a REACHABLE feed is aged_out once its age exceeds
# the max window across its vouching feeds, else retracted (a within-window
# withdrawal). Thresholds live here, not in prose.
USGS_WINDOW_HOURS: float = 72.0
GDACS_WINDOW_DAYS: float = 4.0

# PAGER colour severity ranked on the SAME 0/1/2 scale as GDACS_ALERT_RANK, so
# the impact tier can take the max across both feeds. Per the PRD Q5 impact-tier,
# PAGER yellow/green share the low tier below orange (green < yellow are not
# distinguished for up/downgrade purposes). Compared case-insensitively.
PAGER_ALERT_RANK: dict[str, int] = {"green": 0, "yellow": 0, "orange": 1, "red": 2}

# The impact tier at/above which a confirmed event counts as "confirmed-severe"
# for the provisional->confirmed UPGRADE path (orange rank = 1). Keeps the
# classification input in config, not the diff logic.
MATERIAL_IMPACT_TIER: int = 1

# --- Slice 4: urgent-alert decision + push -------------------------------------
# The urgent-push rule is IMPACT-based, never magnitude — a strong but unconfirmed
# quake never fires (no escape hatch; that path is dashboard-only, above). An event
# is SEVERE if its latest GDACS episode alert is Red, OR its USGS PAGER colour is
# Orange/Red (PAGER is human-reviewed, so its presence also confirms the event via
# Slice 3). Compared case-insensitively. GDACS Orange is deliberately NOT severe:
# per the PRD the GDACS urgent trigger is Red only; the clean Orange->Red escalation
# lives on the PAGER axis.
URGENT_GDACS_LEVELS: frozenset[str] = frozenset({"red"})
URGENT_PAGER_LEVELS: frozenset[str] = frozenset({"orange", "red"})

# Rank of an urgent level, so escalation is comparable and one-push-per-event can
# tell "same severity again" (no re-fire) from "genuinely worse" (fire again).
# An event's current urgent level is the MAX-rank qualifying signal.
URGENT_LEVEL_RANK: dict[str, int] = {"orange": 1, "red": 2}

# Default artifact locations, relative to the current working directory.
DEFAULT_DB_PATH: Path = Path("state/ledger.db")
DEFAULT_OUT_PATH: Path = Path("dashboard.html")
# Readable published snapshots the 08:30 brief diffs against (Slice 5). Under
# state/ (git-ignored for now — a produced artifact until the Slice-7 commit
# coordination lands, per implementation-notes.md).
DEFAULT_PUBLISHED_DIR: Path = Path("state/published")

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
