"""The 08:30 brief — READ-ONLY on the ledger, writes disjoint paths (Slices 5-6).

Single-writer discipline (ADR-0006, refined by PRD #3): the fast tick is the sole
writer of ``ledger.db``; the brief only *reads* it and the last published
snapshot, and writes DISJOINT artifacts — today's ``published/<date>.json``,
``dashboard.html``, and (Slice 6) the fuzzy-link decisions file the NEXT tick
applies. To make "read-only" a guarantee rather than a promise, the ledger is
opened with SQLite's ``mode=ro`` URI, so any stray write raises.

The brief NEVER pushes (the urgent path is the fast tick's, per the PRD hybrid
split). Slice 6 wires an INJECTED model (``ChatModel`` | None) into the brief for
JUDGEMENT ONLY: it characterises each material event's impact (the "impact basis")
and tie-breaks fuzzy ReliefWeb links. The model NEVER decides which events are in
scope or their severity — that stays deterministic (Slice 3/5). A missing model,
or a model error (``ChatResult(ok=False)``), degrades to a deterministic
one-line basis, and the brief still renders. Same ledger + frozen clock + a
scripted model ⇒ deterministic WIRING (the prose is not asserted).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader

from . import config
from .briefer import _is_material
from .clock import Clock, format_sgt, to_sgt
from .diff import (
    CHANGE_BUCKETS,
    ONGOING,
    ClassifiedEvent,
    Diff,
    SnapshotEvent,
    build_diff,
)
from .ledger import read_events, reliefweb_links
from .link_decisions import METHOD_MODEL, LinkDecision, write_decisions
from .llm import ChatModel
from .published import load_latest_snapshot, write_snapshot
from .reliefweb import ReliefWebRecord, resolve_glide

# A dedicated env, autoescape unconditionally on (compound ``.html.j2`` names do
# not match select_autoescape()'s default list — same care as briefer.py). Both
# feed text AND model-authored assessments are untrusted, so this matters twice.
_env = Environment(
    loader=PackageLoader("hadr", "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Token budget for a single short impact-assessment call. Generous enough for a
# reasoning model's hidden-reasoning overhead (see llm.DEFAULT_MAX_TOKENS note),
# small enough that the brief stays cheap.
_ASSESS_MAX_TOKENS = 512
_TIEBREAK_MAX_TOKENS = 256


@dataclass(frozen=True)
class BriefResult:
    """Summary of a brief run, for the CLI + tests to assert as data."""

    out_path: str
    snapshot_path: str
    counts: dict[str, int]
    no_material_change: bool
    # Slice 6: fuzzy ReliefWeb link decisions this brief emitted (recorded to the
    # disjoint decisions file for the next tick to apply), as data to assert.
    link_decisions: tuple[LinkDecision, ...] = ()
    decisions_path: str | None = None


def _connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the ledger READ-ONLY. A write attempt raises — the single-writer
    guarantee is enforced by SQLite, not merely by convention."""
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_mag(magnitude: float | None) -> str:
    return f"M{magnitude:.1f}" if magnitude is not None else "M?"


# --- impact basis (Q4): cite the pulled products, deterministically or via model -


def _deterministic_basis(e: SnapshotEvent) -> str:
    """A one-line impact basis from ledger facts alone — the fallback when no
    model is wired or a model call fails. Cites what the feeds carried (GDACS
    episode colour, PAGER colour, magnitude, confirmation), never invents numbers
    (CLAUDE.md: thresholds/facts in code, not prose)."""
    parts: list[str] = []
    if e.gdacs_episodealertlevel:
        parts.append(f"GDACS {e.gdacs_episodealertlevel.title()}")
    if e.pager_alert:
        parts.append(f"PAGER {e.pager_alert.title()}")
    parts.append(_fmt_mag(e.magnitude))
    if e.status:
        parts.append(str(e.status))
    return "; ".join(parts)


def _assessment_messages(e: SnapshotEvent, rw: list[ReliefWebRecord]) -> list[dict]:
    """A compact, deterministic prompt built from ledger facts (+ any linked
    ReliefWeb excerpt). Sorted/fixed shape so the same event yields the same
    prompt. The model is asked for a SHORT impact basis that cites the pulled
    products; it does not decide scope or severity."""
    facts = {
        "title": e.title,
        "place": e.place,
        "magnitude": e.magnitude,
        "gdacs_episode_alert": e.gdacs_episodealertlevel,
        "pager_alert": e.pager_alert,
        "status": e.status,
        "sources": list(e.sources),
    }
    lines = [f"{k}: {facts[k]}" for k in sorted(facts)]
    if rw:
        lines.append("reliefweb_excerpts:")
        lines += [f"- {r.excerpt} ({r.url})" for r in rw]
    user = (
        "Characterise this earthquake's likely humanitarian impact in ONE short "
        "sentence, citing the pulled products (GDACS episode alert / PAGER bins / "
        "ShakeMap exposure) you were given. Do not invent numbers you were not "
        "given; do not restate the magnitude alone.\n\n" + "\n".join(lines)
    )
    return [
        {"role": "system", "content": "You are a humanitarian disaster analyst."},
        {"role": "user", "content": user},
    ]


def _impact_basis(
    model: ChatModel | None, e: SnapshotEvent, rw: list[ReliefWebRecord]
) -> str:
    """The impact basis for one event: the model's one-liner when a model is wired
    and the call succeeds, else the deterministic basis. A model error is DATA —
    it degrades, never crashes (house rule)."""
    fallback = _deterministic_basis(e)
    if model is None:
        return fallback
    result = model.complete(_assessment_messages(e, rw), max_tokens=_ASSESS_MAX_TOKENS)
    if result.ok and (result.text or "").strip():
        return result.text.strip()
    return fallback


# --- fuzzy ReliefWeb link tie-break (Seam 2): emit a recorded, overridable decision


def _tiebreak_messages(r: ReliefWebRecord, candidates: list[SnapshotEvent]) -> list[dict]:
    lines = [f"{c.canonical_id}: {c.title or c.place or '?'} ({_fmt_mag(c.magnitude)})"
             for c in candidates]
    user = (
        "Which canonical event does this ReliefWeb report describe? Reply with "
        "EXACTLY one canonical_id from the list, or NONE if none matches.\n\n"
        f"ReliefWeb: {r.title}\nExcerpt: {r.excerpt}\n\nCandidates:\n"
        + "\n".join(lines)
    )
    return [
        {"role": "system", "content": "You link humanitarian reports to hazard events."},
        {"role": "user", "content": user},
    ]


def _tiebreak(
    model: ChatModel, r: ReliefWebRecord, candidates: list[SnapshotEvent]
) -> str | None:
    """Ask the model which candidate event a fuzzy (no-GLIDE) ReliefWeb item
    describes. The reply is VALIDATED against the real candidate ids — a model may
    only pick an event that exists, never mint one — so a hallucinated or NONE
    answer resolves to no link (deterministic degrade). Returns a canonical_id or
    None."""
    ids = {c.canonical_id for c in candidates}
    result = model.complete(_tiebreak_messages(r, candidates), max_tokens=_TIEBREAK_MAX_TOKENS)
    if not result.ok:
        return None
    choice = (result.text or "").strip()
    return choice if choice in ids else None


def _resolve_reliefweb(
    conn: sqlite3.Connection,
    records: list[ReliefWebRecord],
    current: list[SnapshotEvent],
    recorded: dict[str, str],
    model: ChatModel | None,
) -> tuple[dict[str, list[ReliefWebRecord]], list[LinkDecision]]:
    """Attach each ReliefWeb item to the canonical event it describes and emit
    decisions for the fuzzy residual.

    Resolution order (deterministic first, model last — ADR-0004 two-tier):
      1. a link ALREADY recorded in the ledger (applied by a prior tick),
      2. a deterministic GLIDE join (no model),
      3. the model tie-break for the fuzzy residual -> a RECORDED, overridable
         decision emitted to the disjoint file for the next tick.
    Returns ``{canonical_id: [records]}`` for rendering the excerpts, plus the new
    decisions to persist."""
    by_cid: dict[str, list[ReliefWebRecord]] = {}
    decisions: list[LinkDecision] = []
    present = {e.canonical_id for e in current}
    for r in records:
        cid = recorded.get(r.id) or resolve_glide(conn, r)
        if cid is None and model is not None:
            cid = _tiebreak(model, r, current)
            if cid is not None:
                decisions.append(
                    LinkDecision(
                        reliefweb_id=r.id, canonical_id=cid, method=METHOD_MODEL,
                        note=f"fuzzy tie-break for ReliefWeb {r.id}",
                    )
                )
        if cid is not None and cid in present:
            by_cid.setdefault(cid, []).append(r)
    return by_cid, decisions


# --- view assembly + render ----------------------------------------------------


def _reliefweb_view(records: list[ReliefWebRecord]) -> list[dict]:
    return [
        {"title": r.title, "excerpt": r.excerpt, "url": r.url, "source": r.source}
        for r in records
    ]


def _event_view(
    ce: ClassifiedEvent,
    assessments: dict[str, str],
    reliefweb_by_cid: dict[str, list[ReliefWebRecord]],
) -> dict:
    e: SnapshotEvent = ce.event
    return {
        "mag": _fmt_mag(e.magnitude),
        "title": e.title,
        "place": e.place,
        "detail": ce.detail,
        "impact_basis": assessments.get(e.canonical_id),
        "reliefweb": _reliefweb_view(reliefweb_by_cid.get(e.canonical_id, [])),
    }


def render_brief(
    diff: Diff,
    as_of_utc: datetime,
    *,
    assessments: dict[str, str] | None = None,
    reliefweb_by_cid: dict[str, list[ReliefWebRecord]] | None = None,
) -> str:
    """Pure render of a diff (+ optional impact assessments and linked ReliefWeb
    items) to the brief dashboard HTML. The "what this monitor cannot see"
    disclosure is a deterministic config constant, always rendered."""
    assessments = assessments or {}
    reliefweb_by_cid = reliefweb_by_cid or {}
    change_buckets = [
        {"label": b,
         "events": [_event_view(ce, assessments, reliefweb_by_cid) for ce in diff.get(b)]}
        for b in CHANGE_BUCKETS
    ]
    ongoing = [_event_view(ce, assessments, reliefweb_by_cid) for ce in diff.get(ONGOING)]
    template = _env.get_template("brief.html.j2")
    return template.render(
        as_of=format_sgt(as_of_utc),
        has_material_change=diff.has_material_change,
        change_buckets=change_buckets,
        ongoing=ongoing,
        cannot_see=config.CANNOT_SEE_DISCLOSURE,
    )


def write_brief(
    db_path: str | Path,
    out_path: str | Path,
    published_dir: str | Path,
    clock: Clock,
    *,
    model: ChatModel | None = None,
    reliefweb: list[ReliefWebRecord] | None = None,
    link_decisions_out: str | Path | None = None,
) -> BriefResult:
    """Read the ledger + last snapshot, compute the diff, characterise impact
    (via the injected ``model``, or deterministically), resolve ReliefWeb links,
    render the dashboard, then publish today's snapshot and emit any fuzzy-link
    decisions. Order matters: the snapshot is written LAST and to a date-stamped
    path, so it never becomes its own diff baseline within a run.

    The brief stays READ-ONLY on the ledger — the model's link decisions go to the
    disjoint ``link_decisions_out`` file for the next tick to apply, never a write
    here."""
    conn = _connect_readonly(db_path)
    try:
        current = [SnapshotEvent.from_row(row) for row in read_events(conn)]
        recorded = reliefweb_links(conn)
        reliefweb_by_cid, decisions = _resolve_reliefweb(
            conn, list(reliefweb or []), current, recorded, model
        )
    finally:
        conn.close()

    today = to_sgt(clock.now()).strftime("%Y-%m-%d")
    previous = load_latest_snapshot(published_dir, before=today)
    diff = build_diff(current, previous)

    # Impact basis for each MATERIAL event (headline/confirmed/severe). The model
    # writes the prose; scope + severity stay deterministic (Slice 3/5).
    assessments: dict[str, str] = {}
    for e in current:
        if _is_material(e.status, e.gdacs_episodealertlevel, e.pager_alert, e.magnitude):
            assessments[e.canonical_id] = _impact_basis(
                model, e, reliefweb_by_cid.get(e.canonical_id, [])
            )

    Path(out_path).write_text(
        render_brief(
            diff, clock.now(),
            assessments=assessments, reliefweb_by_cid=reliefweb_by_cid,
        ),
        encoding="utf-8",
    )
    snapshot_path = write_snapshot(published_dir, clock, current)

    decisions_path = None
    if link_decisions_out is not None:
        decisions_path = str(write_decisions(link_decisions_out, decisions))

    return BriefResult(
        out_path=str(out_path),
        snapshot_path=str(snapshot_path),
        counts=diff.counts(),
        no_material_change=not diff.has_material_change,
        link_decisions=tuple(decisions),
        decisions_path=decisions_path,
    )
