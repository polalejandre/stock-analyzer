import pytest
from typer.testing import CliRunner

from analyzer.cli import app
from analyzer.config import PROJECT_ROOT, ConfigError, load_rules, load_settings


def test_settings_read_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=StockAnalyzer me@test.invalid\nFRED_API_KEY=abc\n")
    s = load_settings(root=tmp_path, environ={})
    assert s.sec_user_agent == "StockAnalyzer me@test.invalid"
    assert s.fred_api_key == "abc"
    assert s.db_path == tmp_path / "data" / "analyzer.db"
    assert s.cache_dir == tmp_path / "data" / "cache"


def test_environment_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=old old@test.invalid\n")
    s = load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "new new@test.invalid"})
    assert s.sec_user_agent == "new new@test.invalid"
    assert s.fred_api_key is None


def test_data_dir_override(tmp_path):
    s = load_settings(
        root=tmp_path,
        environ={"SEC_USER_AGENT": "a a@test.invalid", "ANALYZER_DATA_DIR": str(tmp_path / "x")},
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


def test_fred_api_key_not_in_repr(tmp_path):
    s = load_settings(
        root=tmp_path,
        environ={"SEC_USER_AGENT": "a a@test.invalid", "FRED_API_KEY": "supersecretkey123"},
    )
    assert s.fred_api_key == "supersecretkey123"
    assert "supersecretkey123" not in repr(s)


@pytest.mark.parametrize("agent", ["StockAnalyzer you@example.com", "Me me@EXAMPLE.COM",
                                   "x x@mail.example.com"])
def test_placeholder_contact_email_is_rejected(tmp_path, agent):
    with pytest.raises(ConfigError, match="set a real contact email"):
        load_settings(root=tmp_path, environ={"SEC_USER_AGENT": agent})


def test_env_example_placeholder_is_rejected(tmp_path):
    (tmp_path / ".env").write_text((PROJECT_ROOT / ".env.example").read_text())
    with pytest.raises(ConfigError, match="set a real contact email"):
        load_settings(root=tmp_path, environ={})


def test_wikipedia_contact_is_optional(tmp_path):
    s = load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "a a@test.invalid"})
    assert s.wikipedia_contact is None


def test_wikipedia_contact_read_from_env(tmp_path):
    s = load_settings(
        root=tmp_path,
        environ={"SEC_USER_AGENT": "a a@test.invalid", "WIKIPEDIA_CONTACT": " wiki@test.invalid "},
    )
    assert s.wikipedia_contact == "wiki@test.invalid"


def test_wikipedia_contact_without_email_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="WIKIPEDIA_CONTACT"):
        load_settings(
            root=tmp_path,
            environ={"SEC_USER_AGENT": "a a@test.invalid", "WIKIPEDIA_CONTACT": "nobody"},
        )


def test_wikipedia_contact_placeholder_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="WIKIPEDIA_CONTACT"):
        load_settings(
            root=tmp_path,
            environ={"SEC_USER_AGENT": "a a@test.invalid", "WIKIPEDIA_CONTACT": "you@example.com"},
        )


@pytest.mark.parametrize("contact", ["a@b.c\nX-Evil: y", "two words@b.c", "a@b c.d"])
def test_wikipedia_contact_with_whitespace_is_rejected(tmp_path, contact):
    with pytest.raises(ConfigError, match="WIKIPEDIA_CONTACT"):
        load_settings(root=tmp_path,
                      environ={"SEC_USER_AGENT": "a a@test.invalid", "WIKIPEDIA_CONTACT": contact})


def test_phase2_rules_load():
    rules = load_rules(PROJECT_ROOT / "rules.toml")
    assert rules.gates.guidance_tone_enabled is False
    assert rules.gates.max_value_percentile == 30
    assert rules.research.cooldown_days == 42
    assert rules.research.horizons == (5, 21, 63, 126)
    assert rules.research.win_basis == "excess_spy"
    assert rules.variants.financials.sic_min == 6000
    assert rules.variants.reit.net_debt_ebitda_max == 7.0
    assert rules.variants.excluded_sics == ("6726",)
    assert rules.score.tier_a == 75


def test_regime_rules_load():
    rules = load_rules(PROJECT_ROOT / "rules.toml")
    assert rules.regime.loose_min == 4
    assert rules.regime.tight_max == 2
    assert rules.regime.min_known == 5
    assert rules.regime.median_window == 756
    assert rules.regime.change_window == 65
    assert rules.regime.yoy_window == 252


def test_unknown_rule_key_is_rejected(tmp_path):
    text = (PROJECT_ROOT / "rules.toml").read_text().replace(
        "cooldown_days = 42", "cooldown_days = 42\ncooldwn_days = 1")
    path = tmp_path / "rules.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="research.*cooldwn_days"):
        load_rules(path)


def test_missing_rule_key_is_rejected(tmp_path):
    text = (PROJECT_ROOT / "rules.toml").read_text().replace("cooldown_days = 42\n", "")
    path = tmp_path / "rules.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="research.*cooldown_days"):
        load_rules(path)
