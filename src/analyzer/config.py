"""Settings from the environment (.env) and rules from rules.toml."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_DOMAIN = "example.com"  # the .env.example address; SEC may block it


class ConfigError(Exception):
    """Required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    fred_api_key: str | None = field(repr=False)
    data_dir: Path
    wikipedia_contact: str | None = None  # email for the Wikipedia User-Agent (their policy)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "analyzer.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_settings(root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> Settings:
    """Read `.env` under `root`, then let the process environment override it."""
    env = {**dotenv_values(root / ".env"), **(os.environ if environ is None else environ)}
    user_agent = (env.get("SEC_USER_AGENT") or "").strip()
    if "@" not in user_agent:
        raise ConfigError(
            "SEC_USER_AGENT must be set and include a contact email, "
            "e.g. 'StockAnalyzer you@example.com' (SEC requirement). See .env.example."
        )
    if PLACEHOLDER_DOMAIN in user_agent.lower():
        raise ConfigError(
            "SEC_USER_AGENT still holds the .env.example placeholder address; "
            "set a real contact email (SEC requirement)."
        )
    wikipedia_contact = (env.get("WIKIPEDIA_CONTACT") or "").strip() or None
    if wikipedia_contact is not None and (
        "@" not in wikipedia_contact or PLACEHOLDER_DOMAIN in wikipedia_contact.lower()
    ):
        raise ConfigError(
            "WIKIPEDIA_CONTACT must be a real contact email, or left unset. See .env.example."
        )
    data_dir = Path(env.get("ANALYZER_DATA_DIR") or root / "data")
    return Settings(
        sec_user_agent=user_agent,
        fred_api_key=(env.get("FRED_API_KEY") or "").strip() or None,
        data_dir=data_dir,
        wikipedia_contact=wikipedia_contact,
    )


@dataclass(frozen=True)
class UniverseRules:
    min_dollar_volume: float
    dollar_volume_days: int
    young_max_years: int
    young_min_market_cap: float


@dataclass(frozen=True)
class Rules:
    version: str
    universe: UniverseRules


def load_rules(path: Path = PROJECT_ROOT / "rules.toml") -> Rules:
    data = tomllib.loads(path.read_text())
    u = data["universe"]
    return Rules(
        version=str(data["version"]),
        universe=UniverseRules(
            min_dollar_volume=float(u["min_dollar_volume"]),
            dollar_volume_days=int(u["dollar_volume_days"]),
            young_max_years=int(u["young_max_years"]),
            young_min_market_cap=float(u["young_min_market_cap"]),
        ),
    )
