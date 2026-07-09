"""Slice-7 headline: the deterministic change-gate that decides whether the 08:30
brief wakes — calling NO model. Driven at the feed-fetch seam with a frozen clock
(house style), reusing the Slice-5 two-run fixtures: run 1 seeds a published
snapshot, run 2 mutates the ledger. The gate compares the current ledger against
the last snapshot; the model never enters the decision.

Also covers the single-writer commit helper's PURE decision (which paths a role
owns, and whether there is anything to commit) without touching real git, and a
structural check that the workflow YAML is well-formed.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hadr import config, gate
from hadr.clock import FrozenClock
from hadr.fetch import FixtureFeedSource
from hadr.pipeline import run

_ROOT = Path(__file__).resolve().parents[1]
_FIX = _ROOT / "fixtures"

_DAY1 = FrozenClock(datetime(2026, 7, 7, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-07
_DAY2 = FrozenClock(datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-08
_DAY3 = FrozenClock(datetime(2026, 7, 9, 0, 30, tzinfo=timezone.utc))   # 08:30 SGT 07-09


def _usgs(name):
    return FixtureFeedSource(_FIX / "usgs" / name)


def _gdacs(name):
    return FixtureFeedSource(_FIX / "gdacs" / name)


@pytest.fixture
def env(tmp_path):
    return {
        "db": tmp_path / "ledger.db",
        "pub": tmp_path / "published",
        "tick": tmp_path / "tick.html",
        "dash": tmp_path / "dashboard.html",
    }


def _tick(env, fixture, clock):
    run(_usgs(fixture), clock, env["db"], env["tick"], gdacs_source=_gdacs(fixture))


def _write_snapshot(env, clock):
    """Publish today's snapshot from the current ledger (stand-in for the brief's
    snapshot write — the gate diffs against this)."""
    from hadr.brief import write_brief
    write_brief(env["db"], env["dash"], env["pub"], clock)


# --- the gate's core verdict --------------------------------------------------

def test_gate_reports_change_when_ledger_moved_since_last_snapshot(env):
    # Day 1: seed the ledger and publish its snapshot.
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    # Day 2: the ledger moves (run 2), but no new snapshot is published yet.
    _tick(env, "slice5_run2.json", _DAY2)

    decision = gate.evaluate(env["db"], env["pub"], _DAY2, dashboard_path=env["dash"])
    assert decision.changed is True
    assert decision.flag == "changed=true"
    assert "material change" in decision.reason


def test_gate_reports_no_change_when_ledger_matches_last_snapshot(env):
    # Publish through run 2, so the last snapshot reflects the current ledger.
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    _tick(env, "slice5_run2.json", _DAY2)
    _write_snapshot(env, _DAY2)

    # Day 3: ledger unchanged since the day-2 snapshot -> everything Ongoing.
    decision = gate.evaluate(env["db"], env["pub"], _DAY3, dashboard_path=env["dash"])
    assert decision.changed is False
    assert decision.flag == "changed=false"
    assert decision.counts["Ongoing"] > 0
    assert all(decision.counts[b] == 0 for b in decision.counts if b != "Ongoing")


def test_gate_treats_missing_dashboard_as_change(env):
    # Ledger matches the last snapshot (no material change), but the dashboard
    # artifact is absent -> bootstrap publish.
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    env["dash"].unlink()  # remove the just-rendered dashboard

    decision = gate.evaluate(env["db"], env["pub"], _DAY2, dashboard_path=env["dash"])
    assert decision.changed is True
    assert "absent" in decision.reason


def test_gate_is_deterministic(env):
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    _tick(env, "slice5_run2.json", _DAY2)

    a = gate.evaluate(env["db"], env["pub"], _DAY2, dashboard_path=env["dash"])
    b = gate.evaluate(env["db"], env["pub"], _DAY2, dashboard_path=env["dash"])
    assert a == b


def test_gate_does_not_write_the_ledger(env):
    """The gate is a reader — its read-only connection cannot mutate the ledger."""
    _tick(env, "slice5_run1.json", _DAY1)
    from hadr.ledger import connect

    def fingerprint():
        conn = connect(env["db"])
        try:
            rows = conn.execute(
                "SELECT canonical_id, status, magnitude, last_updated "
                "FROM canonical_events ORDER BY canonical_id"
            ).fetchall()
            return [tuple(r) for r in rows]
        finally:
            conn.close()

    before = fingerprint()
    gate.evaluate(env["db"], env["pub"], _DAY1, dashboard_path=env["dash"])
    assert fingerprint() == before


def test_gate_handles_absent_ledger_without_crashing(env):
    # No ledger, no prior snapshot, but the dashboard exists -> nothing to say.
    env["dash"].write_text("<html>placeholder</html>", encoding="utf-8")
    decision = gate.evaluate(env["db"], env["pub"], _DAY1, dashboard_path=env["dash"])
    assert decision.changed is False


# --- the gate CLI: GITHUB_OUTPUT flag + exit-code branchability ---------------

def _load_cli(name):
    spec = importlib.util.spec_from_file_location(f"{name}_cli", _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_writes_github_output_flag(env, tmp_path, monkeypatch, capsys):
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    _tick(env, "slice5_run2.json", _DAY2)

    gh_output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    cli = _load_cli("change_gate")
    rc = cli.main([
        "--db", str(env["db"]), "--out", str(env["dash"]),
        "--published-dir", str(env["pub"]), "--as-of", "2026-07-08T00:30:00Z",
    ])
    assert rc == 0  # default: exit 0, branch on the output flag
    assert "changed=true" in gh_output.read_text()
    assert "changed=true" in capsys.readouterr().out


def test_cli_exit_code_mode_branches_on_change(env):
    _tick(env, "slice5_run1.json", _DAY1)
    _write_snapshot(env, _DAY1)
    _tick(env, "slice5_run2.json", _DAY2)
    _write_snapshot(env, _DAY2)
    cli = _load_cli("change_gate")

    # No change (ledger matches day-2 snapshot) -> exit 1 in --exit-code mode.
    rc = cli.main([
        "--db", str(env["db"]), "--out", str(env["dash"]),
        "--published-dir", str(env["pub"]), "--as-of", "2026-07-09T00:30:00Z",
        "--exit-code",
    ])
    assert rc == 1


def test_cli_bad_as_of_exits_two(env, capsys):
    cli = _load_cli("change_gate")
    rc = cli.main([
        "--db", str(env["db"]), "--out", str(env["dash"]),
        "--published-dir", str(env["pub"]), "--as-of", "not-a-timestamp",
    ])
    assert rc == 2
    assert "bad --as-of" in capsys.readouterr().err


# --- commit helper: PURE decision (no real git) -------------------------------

def test_commit_role_owned_paths_are_disjoint():
    commit = _load_cli("commit_state")
    tick = set(commit.paths_for_role(commit.ROLE_TICK))
    brief = set(commit.paths_for_role(commit.ROLE_BRIEF))
    assert tick == set(config.TICK_COMMIT_PATHS)
    assert brief == set(config.BRIEF_COMMIT_PATHS)
    # Single-writer: the two writers own disjoint paths (no clobber possible).
    assert tick.isdisjoint(brief)
    assert "state/ledger.db" in tick and "state/ledger.db" not in brief


def test_commit_unknown_role_raises():
    commit = _load_cli("commit_state")
    with pytest.raises(ValueError):
        commit.paths_for_role("nope")


def test_should_commit_only_for_owned_changes():
    commit = _load_cli("commit_state")
    # Tick commits iff ledger.db changed; a dashboard change is the brief's, ignored.
    assert commit.should_commit({"state/ledger.db"}, commit.ROLE_TICK) is True
    assert commit.should_commit({"dashboard.html"}, commit.ROLE_TICK) is False
    assert commit.should_commit(set(), commit.ROLE_TICK) is False
    # Brief commits for dashboard OR a nested snapshot file, never for ledger.db.
    assert commit.should_commit({"dashboard.html"}, commit.ROLE_BRIEF) is True
    assert commit.should_commit({"state/published/2026-07-09.json"}, commit.ROLE_BRIEF) is True
    assert commit.should_commit({"state/ledger.db"}, commit.ROLE_BRIEF) is False


# --- the workflow is well-formed ----------------------------------------------

def test_sitrep_workflow_is_well_formed():
    text = (_ROOT / ".github" / "workflows" / "sitrep.yml").read_text()
    # The change-gate cron == 08:30 SGT (00:30 UTC), and manual dispatch works.
    assert 'cron: "30 0 * * *"' in text
    assert "workflow_dispatch" in text
    assert "concurrency:" in text
    # The publish + commit steps are BOTH guarded by the gate's output flag.
    assert text.count("steps.gate.outputs.changed == 'true'") == 2
    assert "scripts/change_gate.py" in text
    assert "scripts/brief.py" in text
    assert "commit_state.py --role brief" in text
    # Secrets referenced, never a hard-coded value.
    assert "secrets.OPENCODE_API_KEY" in text

    try:
        import yaml
    except ImportError:
        return  # pyyaml not installed here; the structural check above suffices
    doc = yaml.safe_load(text)
    # `on:` parses as the boolean True key in YAML 1.1 — accept either spelling.
    on = doc.get("on", doc.get(True))
    assert "schedule" in on and "workflow_dispatch" in on
    steps = doc["jobs"]["brief"]["steps"]
    gate_step = next(s for s in steps if s.get("id") == "gate")
    assert "change_gate.py" in gate_step["run"]
    guarded = [s for s in steps if s.get("if") == "steps.gate.outputs.changed == 'true'"]
    assert len(guarded) == 2
