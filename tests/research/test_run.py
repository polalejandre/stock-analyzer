import csv
import dataclasses
import json
import subprocess

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer.research import run as run_mod
from analyzer.research.run import GitError, append_oos_log, git_state, run_research
from analyzer.signals.evaluate import load_panels
from tests.fixture_db import DIP, build_fixture_db, fixture_rules

RULES = fixture_rules()


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    root = tmp_path_factory.mktemp("run")
    conn = build_fixture_db(root / "a.db")
    panels = load_panels(conn, RULES, end=pd.Timestamp("2016-12-30").date())
    return conn, panels, root


def test_in_sample_run_writes_outputs(setup):
    conn, panels, root = setup
    res = run_research(conn, RULES, "in-sample", root / "out", panels=panels,
                       now=pd.Timestamp("2026-10-24 10:00"))
    for name in ("events.csv", "summary.json", "breakdowns.csv", "quintiles.csv"):
        assert (res.out_dir / name).exists()
    summary = json.loads((res.out_dir / "summary.json").read_text())
    assert summary["period"] == "in_sample" and summary["guidance_tone_enabled"] is False
    assert summary["groups"]["setup"]["n"] >= 1
    assert summary["verdict"]["tests"]["n"] is False          # far fewer than 100 events
    events_all = pd.read_csv(res.out_dir / "events.csv")
    assert events_all["regime"].notna().all()               # dislocated_all events too
    for group, share in summary["regime_known_share"].items():
        chosen = events_all[(events_all.group == group) & (events_all.period == "in_sample")]
        assert share == pytest.approx((chosen["regime"] != "unknown").mean())
    events = pd.read_csv(res.out_dir / "events.csv")
    setup_row = events[(events.group == "setup") & (events.period == "in_sample")].iloc[0]
    assert setup_row["symbol"] == DIP and "percentile" in setup_row["reasons"]
    assert setup_row["swing_outcome"] in {"target", "stop", "time", "open", "no_fill",
                                          "delisted", "no_data"}


def test_oos_log_appends_one_row(setup, tmp_path):
    conn, panels, root = setup
    res = run_research(conn, RULES, "oos", root / "out2", panels=panels,
                       now=pd.Timestamp("2026-10-24 11:00"))
    log = tmp_path / "oos-runs.csv"
    append_oos_log(res, "abc123", path=log)
    append_oos_log(res, "abc123", path=log)
    rows = list(csv.DictReader(log.open()))
    assert len(rows) == 2 and rows[0]["commit"] == "abc123" and rows[0]["verdict"] == "fail"


def test_cli_refuses_oos_with_uncommitted_rules(monkeypatch):
    from analyzer.cli import app
    monkeypatch.setattr(run_mod, "git_state", lambda *a, **k: ("abc", True, False))
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos"])
    assert result.exit_code == 2
    assert "uncommitted" in result.output


def test_cli_refuses_custom_rules_for_oos(tmp_path):
    from analyzer.cli import app
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos", "--rules",
                                      str(tmp_path / "x.toml")])
    assert result.exit_code == 2 and "rules.toml" in result.output


def test_in_sample_never_reads_oos_prices(setup):
    conn, panels, root = setup
    rules = dataclasses.replace(RULES, research=dataclasses.replace(
        RULES.research, oos_start="2016-06-01"))
    res = run_research(conn, rules, "in-sample", root / "out3", panels=panels,
                       now=pd.Timestamp("2026-10-24 12:00"))
    events = pd.read_csv(res.out_dir / "events.csv", parse_dates=["date"])
    assert len(events) and (events["date"] < pd.Timestamp("2016-06-01")).all()
    assert not set(events["period"]) & {"oos", "oos_incomplete"}
    assert pd.Timestamp(res.summary["dates"][1]) < pd.Timestamp("2016-06-01")
    # and when it loads the panels itself
    res2 = run_research(conn, rules, "in-sample", root / "out4",
                        now=pd.Timestamp("2026-10-24 12:30"))
    assert pd.Timestamp(res2.summary["dates"][1]) < pd.Timestamp("2016-06-01")


def _repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *a],
                       cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "rules.toml").write_text("a = 1\n")
    (tmp_path / "addendum.md").write_text("x\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    return git


PATHS = ("rules.toml", "addendum.md")


def test_git_state_clean_and_dirty_variants(tmp_path):
    git = _repo(tmp_path)
    commit, dirty, tree = git_state(tmp_path, PATHS)
    assert len(commit) == 40 and not dirty and not tree
    rules = tmp_path / "rules.toml"
    rules.write_text("a = 2\n")                       # modified
    assert git_state(tmp_path, PATHS)[1] is True
    git("add", "rules.toml")                          # staged
    assert git_state(tmp_path, PATHS)[1] is True
    git("commit", "-q", "-m", "c")
    assert git_state(tmp_path, PATHS)[1] is False
    rules.unlink()                                    # deleted
    assert git_state(tmp_path, PATHS)[1] is True
    git("checkout", "rules.toml")
    git("rm", "-q", "--cached", "rules.toml")         # untracked
    assert git_state(tmp_path, PATHS)[1] is True


def test_git_state_dirty_tree_flag(tmp_path):
    _repo(tmp_path)
    (tmp_path / "src" / "m.py").write_text("x = 2\n")
    _, dirty, tree = git_state(tmp_path, PATHS)
    assert tree is True and dirty is False


def test_git_state_outside_a_repo_raises(tmp_path):
    with pytest.raises(GitError):
        git_state(tmp_path, PATHS)


def test_cli_reports_git_failure(monkeypatch):
    from analyzer.cli import app

    def boom(*a, **k):
        raise GitError("no git")
    monkeypatch.setattr(run_mod, "git_state", boom)
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos"])
    assert result.exit_code == 2 and "no git" in result.output


def test_cli_oos_success_logs_before_printing(setup, tmp_path, monkeypatch):
    from analyzer.cli import app
    conn, _panels, root = setup
    data = tmp_path / "data"
    data.mkdir()
    build_fixture_db(data / "analyzer.db").close()
    real = run_mod.PROJECT_ROOT / "docs" / "research" / "oos-runs.csv"
    before = real.read_bytes()
    log = tmp_path / "oos-runs.csv"
    monkeypatch.setattr(run_mod, "OOS_LOG", log)
    monkeypatch.setattr(run_mod, "git_state", lambda *a, **k: ("stubbed", False, True))
    monkeypatch.setenv("SEC_USER_AGENT", "Test tester@corp.test")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(data))
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos"])
    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(log.open()))
    assert [r["verdict"] for r in rows] == ["started", "fail"]      # started row, then the result
    assert rows[0]["run_id"] == rows[1]["run_id"] and rows[0]["n"] == ""
    assert all(r["commit"] == "stubbed" and r["dirty_tree"] == "True" for r in rows)
    assert "coverage" in result.output and "win_basis" in result.output
    assert result.output.index("logged in") < result.output.index("verdict:")
    assert real.read_bytes() == before
    assert list((data / "research").iterdir())


def test_cli_refuses_rules_whose_research_section_differs(tmp_path, monkeypatch):
    from analyzer.cli import app
    data = tmp_path / "data"
    build_fixture_db(data / "analyzer.db").close()
    monkeypatch.setenv("SEC_USER_AGENT", "Test tester@corp.test")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(data))
    text = (run_mod.PROJECT_ROOT / "rules.toml").read_text()
    moved = tmp_path / "moved.toml"
    moved.write_text(text.replace('oos_start = "2022-01-01"', 'oos_start = "2026-01-01"'))
    result = CliRunner().invoke(app, ["research", "run", "--rules", str(moved)])
    assert result.exit_code == 2 and "[research]" in result.output
    assert not (data / "research").exists()                 # refused before anything ran


def test_committed_research_rules_need_git(tmp_path):
    with pytest.raises(GitError):
        run_mod.committed_research_rules(tmp_path)


def test_cli_oos_crash_after_start_leaves_the_started_row(tmp_path, monkeypatch):
    from analyzer.cli import app
    data = tmp_path / "data"
    build_fixture_db(data / "analyzer.db").close()
    real = run_mod.PROJECT_ROOT / "docs" / "research" / "oos-runs.csv"
    before = real.read_bytes()
    log = tmp_path / "oos-runs.csv"
    monkeypatch.setattr(run_mod, "OOS_LOG", log)
    monkeypatch.setattr(run_mod, "git_state", lambda *a, **k: ("abc", False, False))

    def crash(*a, **k):
        assert list(csv.DictReader(log.open()))[0]["verdict"] == "started"   # logged before
        raise RuntimeError("boom after reading out-of-sample prices")
    monkeypatch.setattr(run_mod, "run_research", crash)
    monkeypatch.setenv("SEC_USER_AGENT", "Test tester@corp.test")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(data))
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos"])
    assert result.exit_code != 0 and isinstance(result.exception, RuntimeError)
    rows = list(csv.DictReader(log.open()))
    assert len(rows) == 1 and rows[0]["verdict"] == "started" and rows[0]["commit"] == "abc"
    assert rows[0]["run_id"].endswith("-oos") and rows[0]["rules_version"]
    assert list(rows[0]) == run_mod.OOS_FIELDS
    assert real.read_bytes() == before
