"""Settings from the environment (.env) and rules from rules.toml."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EMAIL = re.compile(r"[^\s@]+@[^\s@]+")  # no whitespace: the value goes into an HTTP header
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
        not EMAIL.fullmatch(wikipedia_contact) or PLACEHOLDER_DOMAIN in wikipedia_contact.lower()
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
class FeatureRules:
    high_window: int
    shock_day_drop: float
    correction_drawdown: float
    year_days: int
    percentile_window: int
    percentile_min_obs: int
    history_min_days: int
    peer_min_size: int
    ttm_max_age_days: int
    shares_max_age_days: int
    rsi_period: int
    atr_period: int
    ma_period: int
    pivot_bars: int
    weekly_pivot_bars: int
    divergence_lookback: int
    weekly_divergence_lookback: int
    reclaim_bars: int
    rs_short: int
    rs_long: int
    dv_short: int
    dv_long: int
    hot_rank: float
    cold_rank: float
    flow_min_industries: int
    hot_dv_trend: float
    accumulation_low: float
    accumulation_high: float
    insider_min_buyers: int
    insider_exec_min_usd: float


@dataclass(frozen=True)
class GateRules:
    max_value_percentile: float
    min_cheaper_metrics: int
    min_value_metrics: int
    min_drawdown: float
    min_gap_vs_etf: float
    margin_drop: float
    roe_drop: float
    guidance_tone_enabled: bool
    min_reward_risk: float


@dataclass(frozen=True)
class ScoreRules:
    weight_value: float
    weight_quality: float
    weight_dislocation: float
    weight_flow: float
    weight_technical: float
    insider_bonus: float
    tier_a: float
    tier_b: float
    dislocation_correction_drift: float
    dislocation_idiosyncratic_drift: float
    flow_hot: float
    flow_neutral: float
    flow_cold: float
    growth_scale: float
    margin_scale: float
    roe_scale: float


@dataclass(frozen=True)
class FinancialsRules:
    sic_min: int
    sic_max: int


@dataclass(frozen=True)
class ReitRules:
    sic: str
    net_debt_ebitda_max: float


@dataclass(frozen=True)
class VariantRules:
    standard_net_debt_ebitda_max: float
    excluded_sics: tuple[str, ...]
    financials: FinancialsRules
    reit: ReitRules


@dataclass(frozen=True)
class PlanRules:
    entry_zone_atr: float
    swing_stop_atr: float
    position_stop_atr: float
    swing_retrace: float
    swing_time_stop: int
    position_time_stop: int


@dataclass(frozen=True)
class ResearchRules:
    panel_start: str
    start: str
    oos_start: str
    cooldown_days: int
    horizons: tuple[int, ...]
    go_horizon: int
    purge_horizon: int
    bootstrap_samples: int
    bootstrap_seed: int
    min_events: int
    min_win_rate: float
    win_basis: str


@dataclass(frozen=True)
class Rules:
    version: str
    universe: UniverseRules
    features: FeatureRules
    gates: GateRules
    score: ScoreRules
    variants: VariantRules
    plans: PlanRules
    research: ResearchRules


def _section(cls, table: dict, name: str, nested: dict | None = None):
    """Build a rules dataclass from one TOML table. Unknown and missing keys are errors, so a
    typo can never silently fall back to a default."""
    nested = nested or {}
    names = {f.name for f in fields(cls)}
    plain = {k: v for k, v in table.items() if k not in nested}
    unknown = sorted(set(plain) - names)
    missing = sorted(names - set(plain) - set(nested))
    if unknown or missing:
        raise ValueError(f"rules.toml [{name}]: unknown keys {unknown}, missing keys {missing}")
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in plain.items()}
    return cls(**values, **nested)


def load_rules(path: Path = PROJECT_ROOT / "rules.toml") -> Rules:
    data = tomllib.loads(path.read_text())
    v = data["variants"]
    variants = _section(VariantRules, v, "variants", nested={
        "financials": _section(FinancialsRules, v["financials"], "variants.financials"),
        "reit": _section(ReitRules, v["reit"], "variants.reit"),
    })
    return Rules(
        version=str(data["version"]),
        universe=_section(UniverseRules, data["universe"], "universe"),
        features=_section(FeatureRules, data["features"], "features"),
        gates=_section(GateRules, data["gates"], "gates"),
        score=_section(ScoreRules, data["score"], "score"),
        variants=variants,
        plans=_section(PlanRules, data["plans"], "plans"),
        research=_section(ResearchRules, data["research"], "research"),
    )
