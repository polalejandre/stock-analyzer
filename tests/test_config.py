import pytest
from typer.testing import CliRunner

from analyzer.cli import app
from analyzer.config import PROJECT_ROOT, ConfigError, load_rules, load_settings


def test_settings_read_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=StockAnalyzer me@example.com\nFRED_API_KEY=abc\n")
    s = load_settings(root=tmp_path, environ={})
    assert s.sec_user_agent == "StockAnalyzer me@example.com"
    assert s.fred_api_key == "abc"
    assert s.db_path == tmp_path / "data" / "analyzer.db"
    assert s.cache_dir == tmp_path / "data" / "cache"


def test_environment_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=old old@example.com\n")
    s = load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "new new@example.com"})
    assert s.sec_user_agent == "new new@example.com"
    assert s.fred_api_key is None


def test_data_dir_override(tmp_path):
    s = load_settings(
        root=tmp_path,
        environ={"SEC_USER_AGENT": "a a@example.com", "ANALYZER_DATA_DIR": str(tmp_path / "x")},
    )
    assert s.data_dir == tmp_path / "x"


def test_missing_contact_email_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="contact email"):
        load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "StockAnalyzer"})


def test_rules_file_loads():
    rules = load_rules(PROJECT_ROOT / "rules.toml")
    assert rules.version
    assert rules.universe.min_dollar_volume == 25_000_000
    assert rules.universe.dollar_volume_days == 20
    assert rules.universe.young_max_years == 5
    assert rules.universe.young_min_market_cap == 2_000_000_000


def test_cli_help():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Stock Analyzer" in result.output
