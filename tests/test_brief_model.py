"""Seam 2, the LLM boundary at 08:30: the brief-with-model WIRING. Tests assert
STRUCTURE — that the dashboard shows an "impact basis", the "what this monitor
cannot see" disclosure, and any linked ReliefWeb item as excerpt + link +
attribution — and that a fuzzy tie-break EMITS a recorded, overridable decision.
Never the generated prose (the model is a scripted fake).

Failures are data: a model error (ChatResult ok=False) degrades to the
deterministic basis and the brief still renders. No network, no key.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hadr.brief import write_brief
from hadr.clock import FrozenClock
from hadr.ledger import apply_link_decisions, connect, reconcile_gdacs, reliefweb_links
from hadr.llm import ChatResult
from hadr.model import GdacsRecord
from hadr.reliefweb import ReliefWebRecord, load_fixture

_FIX = Path(__file__).resolve().parents[1] / "fixtures"
_CLOCK = FrozenClock(datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc))

_GLIDE = "EQ-2026-000999-CHL"
_CID = "gdacs:EQ:1560999"


class FakeModel:
    """A scripted ChatModel: one canned assessment for impact calls, one fixed
    canonical_id for the fuzzy tie-break. Records calls so the wiring is
    inspectable. The `ok` flag lets a test force a model failure."""

    def __init__(self, *, assessment="SCRIPTED IMPACT ASSESSMENT", link_choice=None, ok=True):
        self.assessment = assessment
        self.link_choice = link_choice
        self.ok = ok
        self.calls: list[list[dict]] = []

    def complete(self, messages, *, tools=None, max_tokens=2048):
        self.calls.append(messages)
        if not self.ok:
            return ChatResult(ok=False, error="scripted failure")
        user = messages[-1]["content"]
        if "canonical_id from the list" in user:        # a link tie-break call
            return ChatResult(ok=True, text=self.link_choice or "NONE")
        return ChatResult(ok=True, text=self.assessment)  # an impact-basis call


def _seed(tmp_path, *, glide=_GLIDE, level="Red"):
    """A single material GDACS event carrying a GLIDE, so a ReliefWeb item with
    that GLIDE links deterministically."""
    db = tmp_path / "ledger.db"
    rec = GdacsRecord(
        eventtype="EQ", eventid="1560999", episodeid="1730999", glide=glide,
        name="Earthquake near Antofagasta, Chile", alertlevel=level,
        episodealertlevel=level, alertscore=2.0, episodealertscore=2.0,
        country="Chile", iso3="CHL", source="GFZ", sourceid="gfz1560999",
        magnitude=6.5, origin_time_ms=1783463400000, longitude=-70.0, latitude=-23.9,
        depth_km=30.0, is_temporary=False,
    )
    conn = connect(db)
    try:
        reconcile_gdacs(conn, [rec], _CLOCK)
    finally:
        conn.close()
    return db


def _paths(tmp_path):
    return {"out": tmp_path / "brief.html", "pub": tmp_path / "published",
            "dfile": tmp_path / "link_decisions.json"}


# --- impact basis + disclosure wiring -----------------------------------------


def test_brief_with_model_shows_impact_basis_and_disclosure(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    model = FakeModel()
    write_brief(db, p["out"], p["pub"], _CLOCK, model=model)
    html = p["out"].read_text()

    # The impact-basis block is wired in (structure, not the prose bytes).
    assert 'class="impact-basis"' in html
    assert "Impact basis" in html
    # The model was actually consulted for the material event.
    assert any("humanitarian impact" in m[-1]["content"] for m in model.calls)

    # The static "cannot see" disclosure is always present, with its content.
    assert "What this monitor cannot see" in html
    assert "floods" in html and "M4.5" in html and "tsunami" in html


def test_model_failure_degrades_to_deterministic_basis(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    write_brief(db, p["out"], p["pub"], _CLOCK, model=FakeModel(ok=False))
    html = p["out"].read_text()
    # Still renders, no crash; the deterministic basis (from facts) stands in.
    assert 'class="impact-basis"' in html
    assert "GDACS Red" in html            # deterministic fallback cites the facts
    assert "What this monitor cannot see" in html


def test_no_model_uses_deterministic_basis(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    write_brief(db, p["out"], p["pub"], _CLOCK)   # model=None
    html = p["out"].read_text()
    assert 'class="impact-basis"' in html
    assert "GDACS Red" in html


# --- ReliefWeb: excerpt + link + attribution, deterministic GLIDE, no model ---


def test_reliefweb_glide_link_renders_excerpt_link_attribution_without_model(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    records = load_fixture(_FIX / "reliefweb" / "slice6.json")
    # model=None: the GLIDE-carrying item must still link (deterministic join).
    write_brief(db, p["out"], p["pub"], _CLOCK, reliefweb=records)
    html = p["out"].read_text()

    assert "structural damage in coastal districts" in html      # excerpt
    assert "reliefweb.int/report/chile/antofagasta" in html      # link
    assert "Source: ReliefWeb" in html                           # attribution
    # Excerpt only — never a wholesale body (there is no full-body field at all).
    assert 'class="reliefweb"' in html


# --- fuzzy residual: model tie-break emits a recorded, overridable decision ---


def test_fuzzy_tiebreak_emits_recorded_decision(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    # Only the no-GLIDE item; the model resolves it to the real canonical event.
    fuzzy = [ReliefWebRecord(id="rw-mystery", title="Snapshot", url="https://reliefweb.int/x",
                             excerpt="a strong offshore quake", glide=None)]
    model = FakeModel(link_choice=_CID)
    result = write_brief(db, p["out"], p["pub"], _CLOCK, model=model,
                         reliefweb=fuzzy, link_decisions_out=p["dfile"])

    # A recorded decision was emitted to the disjoint file (not the ledger).
    assert len(result.link_decisions) == 1
    d = result.link_decisions[0]
    assert (d.reliefweb_id, d.canonical_id) == ("rw-mystery", _CID)
    assert Path(result.decisions_path).exists()

    # The brief is read-only: the ledger has NO reliefweb link yet — the decision
    # is applied by the next tick. Prove that applying it links correctly (Seam 2).
    conn = connect(db)
    try:
        assert reliefweb_links(conn) == {}
        from hadr.link_decisions import load_decisions
        apply_link_decisions(conn, load_decisions(p["dfile"]))
        assert reliefweb_links(conn) == {"rw-mystery": _CID}
    finally:
        conn.close()


def test_hallucinated_tiebreak_is_rejected_no_decision(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    fuzzy = [ReliefWebRecord(id="rw-mystery", title="Snapshot", url="u",
                             excerpt="x", glide=None)]
    # The model picks an id that is not among the candidates -> rejected.
    model = FakeModel(link_choice="gdacs:EQ:does-not-exist")
    result = write_brief(db, p["out"], p["pub"], _CLOCK, model=model,
                         reliefweb=fuzzy, link_decisions_out=p["dfile"])
    assert result.link_decisions == ()   # no link minted for a hallucinated event


def test_model_and_reliefweb_text_is_autoescaped(tmp_path):
    """Model prose AND ReliefWeb text are untrusted third-party content: a
    regression away from autoescape=True would reopen a stored-XSS hole."""
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    evil = "<script>alert(1)</script>"
    rw = [ReliefWebRecord(id="rw-antofagasta", title=evil, url="u", excerpt=evil, glide=_GLIDE)]
    write_brief(db, p["out"], p["pub"], _CLOCK, model=FakeModel(assessment=evil), reliefweb=rw)
    html = p["out"].read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_glide_link_needs_no_decision(tmp_path):
    db = _seed(tmp_path)
    p = _paths(tmp_path)
    glide_item = [ReliefWebRecord(id="rw-antofagasta", title="Flash Update", url="u",
                                  excerpt="damage", glide=_GLIDE)]
    result = write_brief(db, p["out"], p["pub"], _CLOCK, model=FakeModel(),
                         reliefweb=glide_item, link_decisions_out=p["dfile"])
    # GLIDE resolves deterministically -> no model tie-break, no decision emitted.
    assert result.link_decisions == ()
