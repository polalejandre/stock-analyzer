import json
import time

from typer.testing import CliRunner

from analyzer.cli import app
from analyzer.config import PROJECT_ROOT
from analyzer.research.run import OOS_LOG
from tests.fixture_db import build_fixture_db


def test_cli_in_sample_run_on_the_fixture(tmp_path, monkeypatch):
    data = tmp_path / "data"
    build_fixture_db(data / "analyzer.db").close()
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@test.invalid")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(data))
    rules = tmp_path / "rules.toml"
    rules.write_text((PROJECT_ROOT / "rules.toml").read_text().replace(
        'panel_start = "2009-01-01"', 'panel_start = "2012-01-01"'))
    oos_before = OOS_LOG.read_bytes()
    started = time.monotonic()
    result = CliRunner().invoke(app, ["research", "run", "--rules", str(rules)])
    elapsed = time.monotonic() - started
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "IN-SAMPLE (tuning only)"
    assert any(line.startswith("guidance_tone_enabled=") and "win_basis=" in line for line in lines)
    assert any(line.startswith("coverage ") for line in lines)
    assert "verdict: FAIL (in-sample: tuning only)" in lines
    assert not any("logged in" in line for line in lines)
    out = next((data / "research").iterdir())
    assert json.loads((out / "summary.json").read_text())["groups"]["setup"]["n"] >= 1
    assert elapsed < 60
    assert OOS_LOG.read_bytes() == oos_before
