import csv
import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer.research import run as run_mod
from analyzer.research.run import append_oos_log, run_research
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
    monkeypatch.setattr(run_mod, "git_state", lambda *a, **k: ("abc", True))
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos"])
    assert result.exit_code == 2
    assert "uncommitted" in result.output


def test_cli_refuses_custom_rules_for_oos(tmp_path):
    from analyzer.cli import app
    result = CliRunner().invoke(app, ["research", "run", "--period", "oos", "--rules",
                                      str(tmp_path / "x.toml")])
    assert result.exit_code == 2 and "rules.toml" in result.output
