"""CLI-level tests for scripts/run.py — exit codes and end-to-end wiring.

The scripts layer had no coverage: the --as-of error path (exit 2), the happy
path (exit 0 + artifacts produced), and the skip-and-count degradation are all
exercised here without touching the network (fixture source, frozen --as-of).
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_FIX = _ROOT / "fixtures" / "usgs"


def _load_run():
    spec = importlib.util.spec_from_file_location("run_cli", _ROOT / "scripts" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _argv(fixture, tmp_path, *extra):
    return [
        "--fixture", str(_FIX / fixture),
        "--db", str(tmp_path / "ledger.db"),
        "--out", str(tmp_path / "dashboard.html"),
        "--as-of", "2026-07-08T00:30:00Z",
        *extra,
    ]


def test_happy_path_exit_zero_and_artifacts(tmp_path, capsys):
    run = _load_run()
    rc = run.main(_argv("all_day.json", tmp_path))
    assert rc == 0
    assert (tmp_path / "ledger.db").exists()
    html = (tmp_path / "dashboard.html").read_text()
    assert "as of 2026-07-08 08:30 SGT" in html
    out = capsys.readouterr().out
    assert "feed=ok" in out and "rows_written=3" in out


def test_bad_as_of_exits_two(tmp_path, capsys):
    run = _load_run()
    rc = run.main(
        ["--fixture", str(_FIX / "all_day.json"),
         "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "d.html"),
         "--as-of", "not-a-timestamp"]
    )
    assert rc == 2
    assert "bad --as-of" in capsys.readouterr().err


def test_naive_as_of_exits_two(tmp_path, capsys):
    # A tz-naive timestamp makes FrozenClock reject it -> clean exit 2, no traceback.
    run = _load_run()
    rc = run.main(
        ["--fixture", str(_FIX / "all_day.json"),
         "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "d.html"),
         "--as-of", "2026-07-08T00:30:00"]
    )
    assert rc == 2


def test_requires_a_source(tmp_path):
    # --fixture / --live are a required mutually-exclusive group.
    run = _load_run()
    with pytest.raises(SystemExit):
        run.main(["--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "d.html")])


def test_malformed_feature_run_is_graceful_and_idempotent(tmp_path, capsys):
    run = _load_run()
    rc = run.main(_argv("all_day_one_bad_feature.json", tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    # 2 good features (M6.0, M5.0) persist; the timeless one is skipped, not fatal.
    assert "rows_written=2" in out
    assert "skipped 1 malformed feature(s)" in out
    first_html = (tmp_path / "dashboard.html").read_text()

    # Re-run: no new writes, byte-identical dashboard.
    rc = run.main(_argv("all_day_one_bad_feature.json", tmp_path))
    assert rc == 0
    assert "rows_written=0" in capsys.readouterr().out
    assert (tmp_path / "dashboard.html").read_text() == first_html
