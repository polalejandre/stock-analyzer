# Phase 2: Features, Signals and Research Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run analyzer research run --period in-sample` replays 2014–2021 day by day over the point-in-time universe, finds dislocated stocks (group 2) and gated setups (group 3), measures what happened next, and writes a report with the go/no-go tests. `--period oos` does the same for 2022 to today and logs the run in `docs/research/oos-runs.csv`.

**Architecture:** Four layers with one direction of dependency: `panel` (reads SQLite into wide date × symbol frames) ← `features` (pure functions over panels) ← `signals` (variants, gates, plans, score; one `evaluate()` entry point) ← `research` (events, outcomes, statistics, coverage, report, CLI). Fundamentals become step series (one row per `filed` date) that are as-of joined onto trading dates. Price-only features run on whole panels; fundamental gates run on the long rows that pass the price-only dislocation gate. The future live scan calls the same `evaluate()` and keeps the last date.

**Tech Stack:** Python 3.12, uv, pandas 2.2+, numpy 2, stdlib `sqlite3`, Typer, pytest, ruff. No new dependencies.

**Spec:** [`docs/specs/2026-10-24-phase-2-design-addendum.md`](../specs/2026-10-24-phase-2-design-addendum.md) (approved 2026-10-24), on top of [`docs/specs/2026-10-10-stock-analyzer-design.md`](../specs/2026-10-10-stock-analyzer-design.md) sections 5–9. Executors read both.

## Global Constraints

- Python `>=3.12`. No new dependencies in `pyproject.toml`.
- Free data only (spec non-goal "Paid data").
- A fundamental value becomes known on its EDGAR `filed` date, never its period-end date (spec 3.3).
- Every threshold and weight lives in `rules.toml`. Bump `version` whenever any value changes.
- `guidance_tone_enabled = false` for the first go/no-go run (addendum 2.3).
- In-sample: events from 2014-01-01 whose 126-day forward window ends before 2022-01-01. Out-of-sample: events from 2022-01-01 with a full 63-day window (addendum 6.3).
- 42-trading-day cooldown per ticker **per group** (addendum 6.2).
- Signal at the close of day t; entry at the open of day t + 1 (addendum 6.2).
- Go/no-go (spec 9.6): group 3 n ≥ 100; median 63-day excess return vs SPY > 0; win rate ≥ 55% at 63 days; median 63-day excess return above group 2's; top score quintile above bottom quintile.
- Default tests never touch the network (the autouse guard in `tests/conftest.py`). Live tests are marked `@pytest.mark.live`.
- Dates are ISO `YYYY-MM-DD` strings in SQLite and `datetime64[ns]` in pandas (`facts_read._DT`).
- Canonical ticker form: uppercase, `.` replaced by `-`.
- Wide panels: index = trading dates (`DatetimeIndex`, from SPY bars), columns = price symbols. Fundamental panels are `float32` to bound memory.
- `data/` is gitignored. Research outputs go to `data/research/<run-id>/`; only `docs/research/oos-runs.csv` is committed.
- Never merge or push without the owner's explicit OK.

## Decisions made while planning (to confirm in review)

1. **CIKs of past S&P 500 members come from the cached Wikipedia revisions.** The S&P 500 article has had a CIK column since 2014-06; the S&P 400 article never had one. The 342 cached revisions are already on disk, so the backfill needs no network. S&P 400 renames go into the manual file `src/analyzer/universe/aliases.toml`. Priced tickers without any snapshot CIK are counted as "identity unverified" in the coverage report.
2. **Membership reasons come from the current page's changes table** (columns Date / Added / Removed / Reason). A removed ticker without a row there gets fate `unknown`.
3. **Snapshot CIKs also fill `tickers.cik`** where SEC's current ticker map no longer knows the ticker (32 priced tickers today). The next `analyzer update --step edgar` then fetches their facts, so delisted names that still have prices get fundamentals.
4. **"One year earlier" is the panel value 252 trading days earlier** (TTM, margins, ROE, FCF). That is the value as it was known a year ago, which is point-in-time by construction and needs no second TTM series. Revenue's "each of the last 2 quarters YoY" rule uses exact quarters from the step series instead (`q0`, `q0_ly`, `q1`, `q1_ly`).
5. **TTM source choice** (addendum 5.1, made precise): at each date, the quarterly TTM is used when its latest quarter ends on or after the latest annual period; otherwise the annual value. Either one is dropped once its `filed` date is more than `ttm_max_age_days` (456, about 15 months) old.
6. **Value metrics are all "price per unit", so lower is always cheaper:** P/E, EV/EBITDA, P/FCF (the inverse of FCF yield; reasons still say "FCF yield"), P/S, P/B, P/FFO.
7. **Peer industry = first three SIC digits.** Four-digit SIC groups are too small to reach 8 members.
8. **Win = 63-day excess return vs SPY > 0.** Spec 9.6 says "win rate ≥ 55% at 63 days" without a basis. Excess is the stricter reading and matches the other go/no-go tests. It is fixed in `rules.toml` (`win_basis = "excess_spy"`) before any run.
9. **Quality sub-score normalization** (spec 7.3 gives none): each sub-score is `clip(0.5 + x / (2 × scale), 0, 1)` with `scale` in `rules.toml` (growth 0.20, margin trend 0.05, ROE trend 0.05). FCF: 1.0 positive and rising, 0.5 positive and falling, 0.25 negative and rising, 0 negative and falling. Balance sheet: 1.0 at net debt / EBITDA ≤ 1, 0 at the variant's cap, linear between.
10. **Weekly bars are usable from the first trading day of the next week.** That is one day later than ideal on a normal Friday, but it never treats a partial week as complete.
11. **EPS quarters use year-to-date differences** (additive), like revenue. A derived Q4 EPS is approximate, because share counts differ by quarter. It feeds only the growth sub-score.
12. **Aliases have no validity dates.** Addendum 3.1 sketched `valid_from` / `valid_to` columns. A rename moves the whole price history to the new symbol (Yahoo keeps ANTM's history under ELV), so one mapping per ticker is enough; a reused ticker is handled by the `recycled` fate instead. The migration is named `003_identity.sql` because it also adds `member_fates` and the snapshot CIK.
13. **Stale cover-page share counts** (addendum 3.3) are counted in the research coverage report ("ticker-days on diluted shares") rather than in `analyzer audit`, because the research run is where the share choice is made. The addendum line is updated in Task 27.

## Review Focus

- A ticker reused by a different company (COR was CoreSite, then Cencora). Expect: when the snapshot CIK differs from the CIK behind today's price series, the ticker's membership is excluded as `recycled`, never scored on another company's prices. Test: Task 3, `test_recycled_ticker_is_excluded`.
- A company that stops filing (acquired, or a filer that went quiet). Expect: its last TTM values stop feeding features once their `filed` date is more than 456 days old. Test: Task 8, `test_ttm_goes_stale_after_max_age`.
- A stock split between the share-count date and the price date. Expect: market cap stays continuous; it does not jump by the split ratio. Test: Task 8, `test_market_cap_is_continuous_across_split`.
- A signal on the last date in the data, or a few days before it. Expect: no entry price, no crash; the event is reported as "window incomplete" and kept out of the go/no-go. Test: Task 22, `test_event_on_last_day_has_no_outcome`.
- Weekly RSI divergence at the end of the data, mid-week. Expect: the partial week is not a bar yet, so the weekly signal does not change when later days of that week arrive. Test: Task 12, `test_weekly_bar_waits_for_next_week`.

## File Structure

```
rules.toml                                       # Task 1: all phase-2 sections; version bump
src/analyzer/config.py                           # Task 1: rule dataclasses and loader
src/analyzer/store/migrations/003_identity.sql   # Task 2: snapshot CIK, ticker_aliases, member_fates
src/analyzer/universe/wikipedia.py               # Task 2: CIK column, changes table, 9-column insert
src/analyzer/universe/aliases.py                 # Task 3: CIK backfill, aliases, fates, identity
src/analyzer/universe/aliases.toml               # Task 3: manual renames (S&P 400)
src/analyzer/universe/registry.py                # Task 3: snapshot CIK fallback for tickers.cik
src/analyzer/pipeline/update.py                  # Task 3: universe step runs sync_aliases
src/analyzer/data/edgar/tags.py                  # Task 4: bank and property concepts
src/analyzer/panel/__init__.py                   # Task 5
src/analyzer/panel/prices.py                     # Task 5: trading dates, PricePanels
src/analyzer/panel/universe.py                   # Task 6: symbol map, entities, membership, liquidity
src/analyzer/panel/steps.py                      # Task 7: pure step-series builders
src/analyzer/panel/fundamentals.py               # Task 8: FundamentalPanels from SQLite
src/analyzer/features/__init__.py                # Task 9
src/analyzer/features/value.py                   # Task 9: ratios, percentiles, peer medians
src/analyzer/features/quality.py                 # Task 10: growth, margins, FCF, leverage, ROE, sub-scores
src/analyzer/features/dislocation.py             # Task 11: drawdown, gap, shape, context, window low
src/analyzer/features/technical.py               # Task 12: RSI, ATR, MA, pivots, divergence, reclaim
src/analyzer/features/flow.py                    # Task 13: industry RS, flow tag, accumulation
src/analyzer/features/insider.py                 # Task 14: cluster buying in the drawdown window
src/analyzer/features/regime.py                  # Task 15: liquidity regime with publication lags
src/analyzer/signals/__init__.py                 # Task 16
src/analyzer/signals/variants.py                 # Task 16: variant per symbol
src/analyzer/signals/gates.py                    # Task 17: gates 1, 3, 4; required inputs; reasons
src/analyzer/signals/plans.py                    # Task 18: swing and position plans, gate 5
src/analyzer/signals/score.py                    # Task 19: components, score, tiers
src/analyzer/signals/evaluate.py                 # Task 20: Panels, load_panels, evaluate
src/analyzer/research/__init__.py                # Task 21
src/analyzer/research/events.py                  # Task 21: groups, cooldown, split and purge
src/analyzer/research/outcomes.py                # Task 22: forward returns, MAE, plan simulation
src/analyzer/research/stats.py                   # Task 23: bootstrap, breakdowns, quintiles, verdict
src/analyzer/research/coverage.py                # Task 24: coverage report
src/analyzer/research/run.py                     # Task 25: run_research, outputs, OOS guard and log
src/analyzer/cli.py                              # Task 25: `analyzer research run`
docs/research/oos-runs.csv                       # Task 25: header only
tests/panel_fixtures.py                          # Task 5: synthetic panels, truncation helper
tests/research/fixture_db.py                     # Task 26: synthetic database for the end-to-end test
docs/plans/2026-10-10-phase-1-status.md          # Task 27: phase-2 status section
```

Test files mirror the source tree: `tests/panel/`, `tests/features/`, `tests/signals/`, `tests/research/`, each with an empty `__init__.py` (the existing test folders have one).

## Shared test helpers (created in Task 5, used from Task 5 on)

`tests/panel_fixtures.py` holds `make_price_panels(...)` and `assert_truncation_invariant(...)`. Every feature task adds one truncation-invariance test with it (addendum 4.5).

---

### Task 1: Phase-2 rules and their loader

**Files:**
- Modify: `rules.toml`
- Modify: `src/analyzer/config.py:69-94`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Rules` with fields `version: str`, `universe: UniverseRules`, `features: FeatureRules`, `gates: GateRules`, `score: ScoreRules`, `variants: VariantRules`, `plans: PlanRules`, `research: ResearchRules`. `VariantRules` has `financials: FinancialsRules`, `reit: ReitRules`, `standard_net_debt_ebitda_max: float`, `excluded_sics: tuple[str, ...]`. `load_rules(path) -> Rules`. Field names are exactly the TOML keys below.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: the three new tests FAIL with `AttributeError: 'Rules' object has no attribute 'gates'` (and similar).

- [ ] **Step 3: Write `rules.toml`**

Replace the file with:

```toml
# Every threshold and weight used by the engine. Bump `version` whenever any value changes.
# Phase-2 values are starting values; they are tuned on the in-sample period only (spec 9.2).
version = "2026.10.24-1"

[universe]
min_dollar_volume = 25_000_000      # average close × volume over `dollar_volume_days`, USD
dollar_volume_days = 20
young_max_years = 5                 # a young listing first traded less than this many years ago
young_min_market_cap = 2_000_000_000

[features]
high_window = 63                    # drawdown from this many trading days' high (spec 5.3)
shock_day_drop = 0.07               # a single-day close-to-close drop this large makes a "shock"
correction_drawdown = 0.08          # SPY this far below its 63-day high = "market correction"
year_days = 252                     # "one year earlier" on a daily panel
percentile_window = 1260            # own-history percentile window (5 years of trading days)
percentile_min_obs = 756            # at least 3 years of history (spec 5.1)
history_min_days = 1095             # first fundamentals filing at least this many days back
peer_min_size = 8                   # industry peer group size before falling back to the sector
ttm_max_age_days = 456              # a TTM or annual value filed longer ago is not used (~15 months)
shares_max_age_days = 456           # same for share counts, measured from their period end
rsi_period = 14
atr_period = 14
ma_period = 50
pivot_bars = 5                      # a swing low/high is the extreme of this many bars each side
weekly_pivot_bars = 2
divergence_lookback = 40            # bars; both pivots of a divergence must be this recent
weekly_divergence_lookback = 26
reclaim_bars = 10
rs_short = 63
rs_long = 126
dv_short = 20
dv_long = 120
hot_rank = 0.8                      # top 20% of industries by relative strength
cold_rank = 0.2
hot_dv_trend = 1.2
accumulation_low = 0.8              # up/down volume ratio that scores 0
accumulation_high = 1.4             # up/down volume ratio that scores 1
insider_min_buyers = 2
insider_exec_min_usd = 100_000

[gates]
max_value_percentile = 30           # gate 1: median own-history percentile at most this
min_cheaper_metrics = 2             # gate 1: cheaper than the peer median on at least this many
min_value_metrics = 2               # gate 1: needs at least this many usable metrics
min_drawdown = 0.15                 # gate 2
min_gap_vs_etf = 0.10               # gate 2: at least this much worse than the reference ETF
margin_drop = 0.05                  # gate 3: TTM operating margin down more than this YoY
roe_drop = 0.05                     # gate 3, financials: TTM ROE down more than this YoY
guidance_tone_enabled = false       # left out of the first go/no-go run (addendum 2.3)
min_reward_risk = 1.5               # gate 5

[score]
weight_value = 30
weight_quality = 30
weight_dislocation = 15
weight_flow = 15
weight_technical = 10
insider_bonus = 5
tier_a = 75
tier_b = 60
dislocation_correction_drift = 1.0
dislocation_idiosyncratic_drift = 0.7
flow_hot = 1.0
flow_neutral = 0.5
flow_cold = 0.2
growth_scale = 0.20                 # +20% growth scores 1.0, -20% scores 0
margin_scale = 0.05
roe_scale = 0.05

[variants]
standard_net_debt_ebitda_max = 4.0
excluded_sics = ["6726"]            # BDCs and closed-end funds

[variants.financials]
sic_min = 6000                      # banks, card issuers, brokers, asset managers, insurers
sic_max = 6411

[variants.reit]
sic = "6798"                        # without a property fact the REIT is a mortgage REIT: excluded
net_debt_ebitda_max = 7.0

[plans]
entry_zone_atr = 0.5
swing_stop_atr = 2.0
position_stop_atr = 3.0
swing_retrace = 0.5
swing_time_stop = 20
position_time_stop = 126

[research]
panel_start = "2009-01-01"          # five years of history before the first events
start = "2014-01-01"
oos_start = "2022-01-01"
cooldown_days = 42
horizons = [5, 21, 63, 126]
go_horizon = 63
purge_horizon = 126
bootstrap_samples = 2000
bootstrap_seed = 7
min_events = 100
min_win_rate = 0.55
win_basis = "excess_spy"            # a win is a 63-day return above SPY's over the same days
```

- [ ] **Step 4: Write the loader**

In `src/analyzer/config.py`, add `from dataclasses import fields` to the imports (keep `dataclass, field`), then replace everything from `@dataclass(frozen=True)\nclass UniverseRules:` to the end of the file with:

```python
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
```

Note: `UniverseRules` keeps its old field order, so the existing `UniverseRules(...)` calls in tests still work. `rules.toml` integers such as `max_value_percentile = 30` stay `int`; Python compares them with floats correctly.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS. Then `uv run pytest -q`: all 445+ tests PASS.

- [ ] **Step 6: Commit**

```bash
git add rules.toml src/analyzer/config.py tests/test_config.py
git commit -m "feat: phase-2 rules sections and a strict loader"
```

---

### Task 2: Snapshot CIKs, aliases and fates in the store; Wikipedia CIK and changes parsing

**Files:**
- Create: `src/analyzer/store/migrations/003_identity.sql`
- Modify: `src/analyzer/universe/wikipedia.py:66-95` (`parse_constituents`), `:194-201` (insert), and add `parse_changes`
- Test: `tests/universe/test_wikipedia.py`, `tests/store/test_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - table `index_snapshots` gains column `cik INTEGER` (NULL when the revision has no CIK column);
  - table `ticker_aliases (ticker TEXT PRIMARY KEY, price_symbol TEXT NOT NULL, cik INTEGER, source TEXT NOT NULL)`, `source` in `'cik' | 'manual'`;
  - table `member_fates (ticker TEXT PRIMARY KEY, fate TEXT NOT NULL, reason TEXT)`, `fate` in `'acquired' | 'other' | 'unknown' | 'recycled'`;
  - `parse_constituents(html) -> DataFrame` with columns `ticker, security, gics_sector, gics_sub_industry, date_added, cik` (`cik` is a Python `int` or `None`);
  - `parse_changes(html) -> DataFrame` with columns `removed_ticker, reason` (one row per removed ticker; the latest row wins).

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_db.py`:

```python
def test_identity_migration_adds_tables(tmp_path):
    from analyzer.store.db import connect, migrate
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(index_snapshots)")]
    assert "cik" in cols
    conn.execute("INSERT INTO ticker_aliases VALUES ('ANTM', 'ELV', 1156039, 'cik')")
    conn.execute("INSERT INTO member_fates VALUES ('ATVI', 'acquired', 'Microsoft acquired')")
```

Append to `tests/universe/test_wikipedia.py`:

```python
from analyzer.universe.wikipedia import parse_changes

CIK_TABLE = "<table><tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>CIK</th></tr>" + "".join(
    f"<tr><td>T{i}</td><td>Co {i}</td><td>Energy</td><td>{1000 + i:010d}</td></tr>" for i in range(60)
) + "</table>"

CHANGES = """
<table>
<tr><th rowspan="2">Date</th><th colspan="2">Added</th><th colspan="2">Removed</th><th rowspan="2">Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>October 13, 2023</td><td>BX</td><td>Blackstone</td><td>ATVI</td><td>Activision</td><td>Microsoft acquired Activision Blizzard.</td></tr>
<tr><td>June 1, 2020</td><td>X</td><td>Xco</td><td>OLD</td><td>Old Co</td><td>Market capitalization change.</td></tr>
<tr><td>May 1, 2019</td><td>Y</td><td>Yco</td><td>OLD</td><td>Old Co</td><td>Earlier removal.</td></tr>
</table>
"""


def test_parse_constituents_reads_cik_column():
    df = parse_constituents(CIK_TABLE)
    assert df.loc[df.ticker == "T5", "cik"].item() == 1005


def test_parse_constituents_without_cik_column_gives_none():
    html = CIK_TABLE.replace("<th>CIK</th>", "<th>Other</th>")
    df = parse_constituents(html)
    assert df["cik"].isna().all()


def test_parse_changes_keeps_latest_reason_per_removed_ticker():
    df = parse_changes(CHANGES)
    reasons = dict(zip(df.removed_ticker, df.reason, strict=True))
    assert reasons["ATVI"] == "Microsoft acquired Activision Blizzard."
    assert reasons["OLD"] == "Market capitalization change."


def test_parse_changes_without_table_is_empty():
    assert parse_changes(CIK_TABLE).empty
```

(`parse_constituents` is already imported at the top of that test file; if not, add it to the import line.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/store/test_db.py tests/universe/test_wikipedia.py -v`
Expected: FAIL (`no such table: ticker_aliases`, `ImportError: cannot import name 'parse_changes'`, `KeyError: 'cik'`).

- [ ] **Step 3: Write the migration**

`src/analyzer/store/migrations/003_identity.sql`:

```sql
-- Company identity for past index members (phase 2, addendum 3.1-3.2).
-- The S&P 500 Wikipedia table has carried each member's CIK since 2014-06; NULL before that and
-- for every S&P 400 revision.
ALTER TABLE index_snapshots ADD COLUMN cik INTEGER;

-- A past member's ticker whose prices live under another symbol (a rename: ANTM -> ELV).
CREATE TABLE ticker_aliases (
    ticker       TEXT PRIMARY KEY,
    price_symbol TEXT NOT NULL,
    cik          INTEGER,
    source       TEXT NOT NULL            -- 'cik' | 'manual'
);

-- Why a past member is left out of research: no prices under any symbol (acquired, other,
-- unknown), or its ticker now belongs to another company (recycled).
CREATE TABLE member_fates (
    ticker TEXT PRIMARY KEY,
    fate   TEXT NOT NULL,                 -- 'acquired' | 'other' | 'unknown' | 'recycled'
    reason TEXT
);
```

- [ ] **Step 4: Parse the CIK column and the changes table**

In `src/analyzer/universe/wikipedia.py`, inside `parse_constituents`, after `added = _find(cols, "date added", "date first added")` add:

```python
        cik_col = _find(cols, "cik")
```

and add this entry to the `pd.DataFrame({...})` dict, after `"date_added"`:

```python
                "cik": (
                    pd.to_numeric(t[cik_col], errors="coerce") if cik_col is not None else None
                ),
```

Then, just before `return out.dropna(...)`, add (an object column keeps Python `int`s; a plain list would become float with NaN):

```python
        out["cik"] = pd.Series([int(v) if pd.notna(v) else None for v in out["cik"]],
                               index=out.index, dtype=object)
```

Add the changes parser after `parse_constituents`:

```python
def parse_changes(html: str) -> pd.DataFrame:
    """Removed tickers and the stated reason, from the "changes" table (two header rows: Date,
    Added/Ticker, Added/Security, Removed/Ticker, Removed/Security, Reason). The table lists the
    newest change first, so the first row of a ticker is its latest removal."""
    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")
    except ValueError:  # no tables at all
        tables = []
    for t in tables:
        if not isinstance(t.columns, pd.MultiIndex):
            continue
        cols = {" ".join(str(p) for p in c).lower(): c for c in t.columns}
        removed = _find(cols, "removed ticker")
        reason = _find(cols, "reason")
        if removed is None or reason is None:
            continue
        out = pd.DataFrame({"removed_ticker": t[removed].map(normalize_ticker),
                            "reason": _text_column(t, reason)})
        return out.dropna(subset=["removed_ticker"]).drop_duplicates(
            "removed_ticker", keep="first").reset_index(drop=True)
    return pd.DataFrame({"removed_ticker": pd.Series(dtype="str"),
                         "reason": pd.Series(dtype="str")})
```

In `sync_index_snapshots`, replace the row build and insert with an explicit column list:

```python
        rows = [(index_name, d.isoformat(), rev.revid, r.ticker, r.security, r.gics_sector,
                 r.gics_sub_industry, r.date_added, r.cik) for r in df.itertuples()]
        with conn:
            conn.execute(
                "DELETE FROM index_snapshots WHERE index_name = ? AND snapshot_date = ?",
                (index_name, d.isoformat()),
            )
            conn.executemany(
                "INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, "
                "security, gics_sector, gics_sub_industry, date_added, cik) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/store tests/universe -v`
Expected: all PASS, including the existing snapshot-sync tests (they now insert 9 columns).

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/store/migrations/003_identity.sql src/analyzer/universe/wikipedia.py \
        tests/store/test_db.py tests/universe/test_wikipedia.py
git commit -m "feat: store snapshot CIKs and parse the Wikipedia changes table"
```

---

### Task 3: Aliases, fates and ticker identity

**Files:**
- Create: `src/analyzer/universe/aliases.py`
- Create: `src/analyzer/universe/aliases.toml`
- Modify: `src/analyzer/universe/registry.py:23-53`
- Modify: `src/analyzer/pipeline/update.py` (`step_universe`)
- Test: `tests/universe/test_aliases.py`, `tests/universe/test_registry.py`

**Interfaces:**
- Consumes: `parse_constituents`, `parse_changes`, `revision_html(client, revid)` from `wikipedia.py`; tables from Task 2.
- Produces:
  - `backfill_snapshot_ciks(conn, client) -> int` (rows updated);
  - `snapshot_ciks(conn) -> dict[str, set[int]]` (ticker → every CIK seen in its snapshots);
  - `change_reasons(conn, client) -> dict[str, str]` (removed ticker → reason, both indexes);
  - `classify_fate(reason: str | None) -> str`;
  - `build_aliases(conn, reasons, manual_path=MANUAL_PATH) -> AliasSummary` (dataclass `aliases: int, recycled: int, excluded: dict[str, int]`);
  - `sync_aliases(conn, client) -> AliasSummary` (the three steps above, in order);
  - `refresh_tickers` falls back to the latest snapshot CIK when SEC's ticker map has none.

- [ ] **Step 1: Write the failing tests**

`tests/universe/test_aliases.py`:

```python
"""Aliases, fates and identity for past index members."""

from analyzer.store.db import connect, migrate
from analyzer.universe.aliases import build_aliases, classify_fate, snapshot_ciks


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    return conn


def _member(conn, ticker, cik=None, index="sp500", snap="2020-01-01"):
    conn.execute("INSERT OR IGNORE INTO index_membership VALUES (?, ?, '2015-01-01', NULL)",
                 (index, ticker))
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, cik) "
                 "VALUES (?, ?, 1, ?, ?)", (index, snap, ticker, cik))


def _priced(conn, symbol, cik=None):
    conn.execute("INSERT INTO prices (symbol, date, close, source) VALUES (?, '2020-01-02', 1, 't')",
                 (symbol,))
    conn.execute("INSERT OR REPLACE INTO tickers (ticker, cik, updated_at) VALUES (?, ?, 'x')",
                 (symbol, cik))


def test_rename_is_aliased_through_the_snapshot_cik(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "ANTM", cik=1156039)
    _priced(conn, "ELV", cik=1156039)
    summary = build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT price_symbol, source FROM ticker_aliases WHERE ticker='ANTM'"
                        ).fetchone() == ("ELV", "cik")
    assert summary.aliases == 1


def test_manual_alias_wins(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "OLD", index="sp400")
    _priced(conn, "NEW", cik=5)
    manual = tmp_path / "aliases.toml"
    manual.write_text('[[alias]]\nticker = "OLD"\nprice_symbol = "NEW"\n')
    build_aliases(conn, reasons={}, manual_path=manual)
    assert conn.execute("SELECT price_symbol, source FROM ticker_aliases").fetchone() == ("NEW", "manual")


def test_delisted_member_gets_a_fate_from_the_reason(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "ATVI", cik=718877)
    summary = build_aliases(conn, reasons={"ATVI": "Microsoft acquired Activision Blizzard."},
                            manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT fate FROM member_fates WHERE ticker='ATVI'").fetchone() == ("acquired",)
    assert summary.excluded == {"acquired": 1}


def test_recycled_ticker_is_excluded(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "COR", cik=1490892)        # CoreSite in the snapshot
    _priced(conn, "COR", cik=1140859)        # today's COR is Cencora
    summary = build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT fate FROM member_fates WHERE ticker='COR'").fetchone() == ("recycled",)
    assert summary.recycled == 1


def test_same_company_ticker_is_kept(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "AAPL", cik=320193)
    _priced(conn, "AAPL", cik=320193)
    build_aliases(conn, reasons={}, manual_path=tmp_path / "none.toml")
    assert conn.execute("SELECT COUNT(*) FROM member_fates").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM ticker_aliases").fetchone() == (0,)


def test_classify_fate():
    assert classify_fate("XYZ Corp. acquired ABC.") == "acquired"
    assert classify_fate("Merger with DEF") == "acquired"
    assert classify_fate("Market capitalization change.") == "other"
    assert classify_fate(None) == "unknown"


def test_snapshot_ciks_collects_every_cik(tmp_path):
    conn = _db(tmp_path)
    _member(conn, "COR", cik=1, snap="2015-01-01")
    _member(conn, "COR", cik=2, snap="2024-01-01")
    assert snapshot_ciks(conn) == {"COR": {1, 2}}
```

Append to `tests/universe/test_registry.py`:

```python
def test_snapshot_cik_fills_a_ticker_sec_no_longer_maps(tmp_path):
    import pandas as pd
    from datetime import date
    from analyzer.store.db import connect, migrate
    from analyzer.universe.registry import refresh_tickers
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, "
                 "security, gics_sector, cik) VALUES ('sp500', '2020-01-01', 1, 'GONE', 'Gone Co', "
                 "'Energy', 4242)")
    empty_map = pd.DataFrame({"ticker": pd.Series(dtype=str), "cik": pd.Series(dtype=int),
                              "name": pd.Series(dtype=str), "exchange": pd.Series(dtype=str)})
    refresh_tickers(conn, empty_map, date(2026, 10, 24))
    assert conn.execute("SELECT cik FROM tickers WHERE ticker='GONE'").fetchone() == (4242,)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/universe/test_aliases.py tests/universe/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.universe.aliases'`, and the registry test with `(None,) != (4242,)`.

- [ ] **Step 3: Write `aliases.py` and the manual file**

`src/analyzer/universe/aliases.toml`:

```toml
# Renamed index members whose old ticker has no prices and no CIK in the Wikipedia snapshots
# (every S&P 400 revision lacks the CIK column). One entry per rename:
#
# [[alias]]
# ticker = "OLD"           # the ticker in the index snapshots
# price_symbol = "NEW"     # the symbol its price history lives under today
#
# Add entries when the research coverage report lists a renamed company under "excluded".
```

`src/analyzer/universe/aliases.py`:

```python
"""Identity of past index members: renames (aliases), why others are excluded (fates), and
tickers that now belong to a different company (recycled)."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from analyzer.data.http import HttpClient
from analyzer.store.db import get_state, set_state
from analyzer.universe.wikipedia import parse_changes, parse_constituents, revision_html

log = logging.getLogger(__name__)

MANUAL_PATH = Path(__file__).with_name("aliases.toml")
PARSED_REVISIONS_KEY = "snapshot_cik_revisions"  # sync_state: JSON list of revids already parsed
ACQUIRED = re.compile(r"\b(acquir\w*|merg\w*|bought|takeover|taken private|buyout)\b", re.I)


@dataclass
class AliasSummary:
    aliases: int = 0
    recycled: int = 0
    excluded: dict[str, int] = field(default_factory=dict)


def backfill_snapshot_ciks(conn, client: HttpClient) -> int:
    """Fill index_snapshots.cik from each revision's own table. Revisions are cached on disk by
    the snapshot sync, so this reads the cache; each revision is parsed once."""
    done = set(json.loads(get_state(conn, PARSED_REVISIONS_KEY) or "[]"))
    revids = [r[0] for r in conn.execute(
        "SELECT DISTINCT revision_id FROM index_snapshots WHERE cik IS NULL")]
    updated = 0
    for revid in revids:
        if revid in done:
            continue
        try:
            df = parse_constituents(revision_html(client, revid))
        except Exception as exc:  # an unreadable revision only leaves its CIKs empty
            log.warning("revision %s: %s: %s", revid, type(exc).__name__, exc)
            continue
        pairs = [(int(c), revid, t) for t, c in zip(df.ticker, df.cik, strict=True) if c is not None]
        with conn:
            conn.executemany("UPDATE index_snapshots SET cik = ? WHERE revision_id = ? AND ticker = ?",
                             pairs)
        updated += len(pairs)
        done.add(revid)
    set_state(conn, PARSED_REVISIONS_KEY, json.dumps(sorted(done)))
    return updated


def snapshot_ciks(conn) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for ticker, cik in conn.execute(
            "SELECT DISTINCT ticker, cik FROM index_snapshots WHERE cik IS NOT NULL"):
        out.setdefault(ticker, set()).add(int(cik))
    return out


def change_reasons(conn, client: HttpClient) -> dict[str, str]:
    """Removed ticker -> reason, from the latest stored revision of each index's article."""
    out: dict[str, str] = {}
    for (revid,) in conn.execute(
            "SELECT revision_id FROM index_snapshots s WHERE snapshot_date = "
            "(SELECT MAX(snapshot_date) FROM index_snapshots WHERE index_name = s.index_name) "
            "GROUP BY index_name"):
        try:
            df = parse_changes(revision_html(client, revid))
        except Exception as exc:
            log.warning("changes table of revision %s: %s: %s", revid, type(exc).__name__, exc)
            continue
        for t, reason in zip(df.removed_ticker, df.reason, strict=True):
            if reason:
                out.setdefault(t, reason)
    return out


def classify_fate(reason: str | None) -> str:
    if not reason:
        return "unknown"
    return "acquired" if ACQUIRED.search(reason) else "other"


def _load_manual(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    data = tomllib.loads(Path(path).read_text())
    return {e["ticker"]: e["price_symbol"] for e in data.get("alias", [])}


def build_aliases(conn, reasons: Mapping[str, str],
                  manual_path: Path = MANUAL_PATH) -> AliasSummary:
    """Rewrite ticker_aliases and member_fates for every ticker ever in an index."""
    priced = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices")}
    current_cik = {t: c for t, c in conn.execute("SELECT ticker, cik FROM tickers") if c is not None}
    members = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM index_membership")}
    symbol_by_cik: dict[int, str] = {}
    for t in sorted(priced):  # deterministic: alphabetical first symbol per CIK
        if t in current_cik:
            symbol_by_cik.setdefault(int(current_cik[t]), t)
    snaps = snapshot_ciks(conn)
    manual = _load_manual(manual_path)

    aliases: list[tuple] = []
    fates: list[tuple] = []
    for t in sorted(members):
        seen = snaps.get(t, set())
        if t in manual:
            aliases.append((t, manual[t], current_cik.get(manual[t]), "manual"))
        elif t in priced:
            cur = current_cik.get(t)
            if seen and cur is not None and int(cur) not in seen:
                target = next((symbol_by_cik[c] for c in sorted(seen) if c in symbol_by_cik), None)
                if target is not None and target != t:
                    aliases.append((t, target, next(iter(sorted(seen))), "cik"))
                else:
                    fates.append((t, "recycled", f"snapshot CIK {sorted(seen)} != price CIK {cur}"))
        else:
            target = next((symbol_by_cik[c] for c in sorted(seen) if c in symbol_by_cik), None)
            if target is not None:
                aliases.append((t, target, next(iter(sorted(seen))), "cik"))
            else:
                reason = reasons.get(t)
                fates.append((t, classify_fate(reason), reason))
    with conn:
        conn.execute("DELETE FROM ticker_aliases")
        conn.execute("DELETE FROM member_fates")
        conn.executemany("INSERT INTO ticker_aliases VALUES (?, ?, ?, ?)", aliases)
        conn.executemany("INSERT INTO member_fates VALUES (?, ?, ?)", fates)
    counts = Counter(f for _, f, _ in fates)
    recycled = counts.pop("recycled", 0)
    return AliasSummary(aliases=len(aliases), recycled=recycled, excluded=dict(counts))


def sync_aliases(conn, client: HttpClient) -> AliasSummary:
    backfill_snapshot_ciks(conn, client)
    return build_aliases(conn, change_reasons(conn, client))
```

- [ ] **Step 4: Snapshot CIK fallback in the registry**

In `src/analyzer/universe/registry.py`, inside `refresh_tickers`, after `sic = dict(...)` add:

```python
    snap_cik = dict(conn.execute(  # ordered by date, so dict() keeps each ticker's latest CIK
        "SELECT ticker, cik FROM index_snapshots WHERE cik IS NOT NULL "
        "ORDER BY snapshot_date").fetchall())
```

and after `cik = young_cik.get(ticker)` (inside `if cik is None:`), add a second fallback:

```python
        if cik is None:
            cik = snap_cik.get(ticker)
```

- [ ] **Step 5: Run aliases at the end of the universe step**

In `src/analyzer/pipeline/update.py`, add `from analyzer.universe.aliases import sync_aliases` to the imports. In `step_universe`, after `res.detail = f"tickers={...}"` add:

```python
    res.attempted += 1
    try:
        summary = sync_aliases(ctx.conn, ctx.clients.wiki)
        res.detail += (f" aliases={summary.aliases} recycled={summary.recycled} "
                       f"excluded={sum(summary.excluded.values())}")
    except Exception as exc:  # identity is rebuilt next run; membership itself is fine
        log.exception("alias sync failed")
        res.failed.append(("aliases", _err(exc)))
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/universe tests/pipeline -v`
Expected: all PASS. If an existing `step_universe` test uses a fake Wikipedia client without cached revisions, `sync_aliases` logs warnings and returns zeros; the step stays healthy.

- [ ] **Step 7: Commit**

```bash
git add src/analyzer/universe/aliases.py src/analyzer/universe/aliases.toml \
        src/analyzer/universe/registry.py src/analyzer/pipeline/update.py \
        tests/universe/test_aliases.py tests/universe/test_registry.py
git commit -m "feat: ticker aliases, member fates and recycled-ticker detection"
```

---

### Task 4: Bank revenue and REIT property concepts

**Files:**
- Modify: `src/analyzer/data/edgar/tags.py:28-67`
- Test: `tests/data/test_tags.py` (create)

**Interfaces:**
- Consumes: `Concept`, `extract_facts`.
- Produces: concepts `net_interest_income` (duration, USD), `noninterest_income` (duration, USD), `real_estate_property` (instant, USD). The tag-map fingerprint changes, so the next `analyzer update --step edgar` re-extracts every company from the cached companyfacts (no extra downloads).

- [ ] **Step 1: Write the failing test**

`tests/data/test_tags.py`:

```python
from analyzer.data.edgar.companyfacts import extract_facts


def _cf(tag, unit_rows):
    return {"facts": {"us-gaap": {tag: {"units": {"USD": unit_rows}}}}}


def test_bank_and_property_concepts_are_extracted():
    q = {"start": "2024-01-01", "end": "2024-03-31", "val": 10.0, "filed": "2024-05-01",
         "form": "10-Q", "accn": "a1"}
    inst = {"end": "2024-03-31", "val": 99.0, "filed": "2024-05-01", "form": "10-Q", "accn": "a1"}
    rows = (extract_facts(_cf("InterestIncomeExpenseNet", [q]), 1)
            + extract_facts(_cf("NoninterestIncome", [q]), 1)
            + extract_facts(_cf("RealEstateInvestmentPropertyNet", [inst]), 1))
    concepts = {r[1] for r in rows}
    assert concepts == {"net_interest_income", "noninterest_income", "real_estate_property"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/data/test_tags.py -v`
Expected: FAIL, `concepts == set()`.

- [ ] **Step 3: Add the concepts**

In `src/analyzer/data/edgar/tags.py`, add to `CONCEPTS` after `shares_outstanding`:

```python
    # Bank revenue = net interest income + noninterest income (addendum 3.3), used when a
    # financial company reports no revenue tag.
    Concept("net_interest_income", "duration", "USD", _g("InterestIncomeExpenseNet")),
    Concept("noninterest_income", "duration", "USD", _g("NoninterestIncome")),
    # An equity REIT owns property; a REIT without it is a mortgage REIT (addendum 5.3).
    Concept("real_estate_property", "instant", "USD", _g(
        "RealEstateInvestmentPropertyNet", "RealEstateInvestmentPropertyAtCost")),
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/data -v`
Expected: all PASS (`TAG_MAP_FINGERPRINT` changes automatically because it hashes `CONCEPTS`).

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/data/edgar/tags.py tests/data/test_tags.py
git commit -m "feat: bank revenue and REIT property concepts in the tag map"
```

---
### Task 5: Price panels and the shared panel test helpers

**Files:**
- Create: `src/analyzer/panel/__init__.py` (empty)
- Create: `src/analyzer/panel/prices.py`
- Create: `tests/panel/__init__.py` (empty), `tests/panel_fixtures.py`
- Test: `tests/panel/test_prices.py`

**Interfaces:**
- Consumes: the `prices` table.
- Produces:
  - `PricePanels` (frozen dataclass; fields `open, high, low, close, adj_close, volume`, each a wide `DataFrame`); properties `dates -> DatetimeIndex`, `adj_open -> DataFrame`; methods `upto(end) -> PricePanels`, `select(symbols) -> PricePanels`;
  - `trading_dates(conn, start: date, end: date, anchor: str = "SPY") -> pd.DatetimeIndex`;
  - `load_price_panels(conn, symbols: Sequence[str], dates: pd.DatetimeIndex) -> PricePanels`;
  - test helpers `random_walk(symbols, n, seed, start) -> DataFrame`, `make_price_panels(closes: DataFrame, volume=1e6) -> PricePanels`, `cut(obj, end)`, `assert_truncation_invariant(compute, cut_dates)`.

- [ ] **Step 1: Write the test helpers**

`tests/panel_fixtures.py`:

```python
"""Synthetic panels and the truncation-invariance check (addendum 4.5)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analyzer.panel.prices import PricePanels


def random_walk(symbols, n: int = 400, seed: int = 0, start: str = "2018-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.02, size=(n, len(symbols)))
    closes = 100 * np.exp(np.cumsum(steps, axis=0))
    return pd.DataFrame(closes, index=pd.bdate_range(start, periods=n), columns=list(symbols))


def make_price_panels(closes: pd.DataFrame, volume: float = 1e6) -> PricePanels:
    """Open = high = low = close; adjusted close = close; constant volume."""
    vol = pd.DataFrame(volume, index=closes.index, columns=closes.columns)
    return PricePanels(open=closes, high=closes, low=closes, close=closes,
                       adj_close=closes, volume=vol)


def cut(obj, end):
    """`obj` as it would look with data only up to `end` (None: unchanged)."""
    if end is None:
        return obj
    if hasattr(obj, "upto"):
        return obj.upto(end)
    return obj.loc[:end]


def _frames(result) -> dict[str, pd.DataFrame]:
    """Every Series/DataFrame in `result` (a frame, a dict, or a dataclass), by name."""
    if isinstance(result, (pd.Series, pd.DataFrame)):
        result = {"value": result}
    elif not isinstance(result, dict):
        result = vars(result)
    return {k: (v.to_frame() if isinstance(v, pd.Series) else v)
            for k, v in result.items() if isinstance(v, (pd.Series, pd.DataFrame))}


def assert_truncation_invariant(compute, cut_dates) -> None:
    """`compute(end)` must build its result from inputs cut at `end` (None = full data). The row
    at each cut date must be identical whether or not later data exists."""
    full = _frames(compute(None))
    assert full, "compute returned no frames"
    for d in cut_dates:
        d = pd.Timestamp(d)
        part = _frames(compute(d))
        for name, frame in full.items():
            pd.testing.assert_series_equal(
                frame.loc[d], part[name].loc[d], check_names=False, check_dtype=False,
                obj=f"{name} at {d.date()}")
```

- [ ] **Step 2: Write the failing tests**

`tests/panel/test_prices.py`:

```python
from datetime import date

import pandas as pd

from analyzer.panel.prices import load_price_panels, trading_dates
from analyzer.store.db import connect, migrate


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    rows = [("SPY", "2024-01-02", 10, 11, 9, 10, 10, 100), ("SPY", "2024-01-03", 10, 11, 9, 10.5, 10.5, 100),
            ("SPY", "2024-01-04", 10, 11, 9, 11, 11, 100),
            ("AAA", "2024-01-02", 20, 21, 19, 20, 19, 5), ("AAA", "2024-01-04", 20, 21, 19, 22, None, 5)]
    conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,'t')", rows)
    return conn


def test_trading_dates_follow_spy(tmp_path):
    dates = trading_dates(_db(tmp_path), date(2024, 1, 1), date(2024, 1, 31))
    assert list(dates.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_panels_align_and_fill_missing_adjusted_close(tmp_path):
    conn = _db(tmp_path)
    dates = trading_dates(conn, date(2024, 1, 1), date(2024, 1, 31))
    p = load_price_panels(conn, ["AAA", "ZZZ"], dates)
    assert list(p.close.columns) == ["AAA", "ZZZ"]
    assert pd.isna(p.close.loc["2024-01-03", "AAA"])          # no bar that day
    assert p.adj_close.loc["2024-01-04", "AAA"] == 22          # NULL adj_close -> close
    assert p.close["ZZZ"].isna().all()                         # unknown symbol: all NaN
    assert p.adj_open.loc["2024-01-02", "AAA"] == 20 * 19 / 20


def test_upto_cuts_every_field(tmp_path):
    conn = _db(tmp_path)
    p = load_price_panels(conn, ["AAA"], trading_dates(conn, date(2024, 1, 1), date(2024, 1, 31)))
    q = p.upto("2024-01-03")
    assert q.volume.index[-1] == pd.Timestamp("2024-01-03")
    assert len(q.open) == 2
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/panel/test_prices.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.panel'`.

- [ ] **Step 4: Write `prices.py`**

`src/analyzer/panel/prices.py`:

```python
"""Wide price panels: one row per trading date, one column per symbol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date

import pandas as pd

ANCHOR = "SPY"
_CHUNK = 900  # SQLite allows 999 host parameters by default
_DT = "datetime64[ns]"
FIELDS = ("open", "high", "low", "close", "adj_close", "volume")


@dataclass(frozen=True)
class PricePanels:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    adj_close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def adj_open(self) -> pd.DataFrame:
        """The open on the dividend-adjusted basis of `adj_close`."""
        return self.open * (self.adj_close / self.close)

    def upto(self, end) -> PricePanels:
        end = pd.Timestamp(end)
        return PricePanels(**{f.name: getattr(self, f.name).loc[:end] for f in fields(self)})

    def select(self, symbols: Sequence[str]) -> PricePanels:
        cols = list(symbols)
        return PricePanels(**{f.name: getattr(self, f.name).reindex(columns=cols)
                              for f in fields(self)})


def trading_dates(conn, start: date, end: date, anchor: str = ANCHOR) -> pd.DatetimeIndex:
    rows = conn.execute("SELECT date FROM prices WHERE symbol = ? AND date >= ? AND date <= ? "
                        "ORDER BY date", (anchor, start.isoformat(), end.isoformat())).fetchall()
    return pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]).astype(_DT), name="date")


def load_price_panels(conn, symbols: Sequence[str], dates: pd.DatetimeIndex) -> PricePanels:
    symbols = list(dict.fromkeys(symbols))
    first, last = dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")
    parts = []
    for i in range(0, len(symbols), _CHUNK):
        chunk = symbols[i:i + _CHUNK]
        parts.append(pd.read_sql_query(
            f"SELECT symbol, date, {', '.join(FIELDS)} FROM prices "
            f"WHERE symbol IN ({', '.join('?' * len(chunk))}) AND date >= ? AND date <= ?",
            conn, params=[*chunk, first, last]))
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["symbol", "date", *FIELDS])
    df["date"] = pd.to_datetime(df["date"]).astype(_DT)
    df["adj_close"] = df["adj_close"].fillna(df["close"])
    out = {}
    for f in FIELDS:
        wide = df.pivot(index="date", columns="symbol", values=f) if len(df) else pd.DataFrame()
        out[f] = wide.reindex(index=dates, columns=symbols).astype("float64")
    return PricePanels(**out)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/panel/test_prices.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/panel tests/panel tests/panel_fixtures.py
git commit -m "feat: wide price panels and truncation-invariance test helpers"
```

---

### Task 6: Universe panels and symbol entities

**Files:**
- Create: `src/analyzer/panel/universe.py`
- Modify: `src/analyzer/universe/industry.py` (add `sic_sector`)
- Test: `tests/panel/test_universe.py`

**Interfaces:**
- Consumes: tables `index_membership`, `ticker_aliases`, `member_fates`, `tickers`, `companies`, `index_snapshots`, `prices`; `UniverseRules`.
- Produces:
  - `symbol_map(conn) -> dict[str, str]` (every ticker ever in an index → its price symbol; tickers with a fate are left out);
  - `membership_panel(conn, dates, symbols_of) -> DataFrame[bool]` (columns = sorted distinct symbols of `symbols_of`);
  - `liquidity_panel(close, volume, rules: UniverseRules) -> DataFrame[bool]`;
  - `load_entities(conn, symbols) -> DataFrame` indexed by symbol, columns `cik` (float, NaN when unknown), `sic` (str or None), `sector` (str or None), `industry` (`"sic3:NNN"` or None), `sector_etf`, `industry_etf`, `ref_etf` (industry ETF, else sector ETF, else `"SPY"`), `identity_verified` (bool);
  - `sic_sector(sic) -> str | None` in `industry.py`.

- [ ] **Step 1: Write the failing tests**

`tests/panel/test_universe.py`:

```python
import pandas as pd

from analyzer.config import UniverseRules
from analyzer.panel.universe import liquidity_panel, load_entities, membership_panel, symbol_map
from analyzer.store.db import connect, migrate

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=3, young_max_years=5,
                      young_min_market_cap=2e9)


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.executemany("INSERT INTO index_membership VALUES (?, ?, ?, ?)", [
        ("sp500", "ANTM", "2015-01-01", "2022-07-01"), ("sp500", "ELV", "2022-07-01", None),
        ("sp500", "ATVI", "2015-01-01", "2023-10-13")])
    conn.execute("INSERT INTO ticker_aliases VALUES ('ANTM', 'ELV', 1156039, 'cik')")
    conn.execute("INSERT INTO member_fates VALUES ('ATVI', 'acquired', 'x')")
    conn.execute("INSERT INTO prices (symbol, date, close, source) VALUES ('ELV', '2015-01-02', 1, 't')")
    conn.execute("INSERT INTO tickers (ticker, cik, gics_sector, sector_etf, industry_etf, updated_at) "
                 "VALUES ('ELV', 1156039, 'Health Care', 'XLV', NULL, 'x')")
    conn.execute("INSERT INTO companies VALUES (1156039, 'Elevance', '6324', 'ins', 'x')")
    conn.execute("INSERT INTO index_snapshots (index_name, snapshot_date, revision_id, ticker, cik) "
                 "VALUES ('sp500', '2020-01-01', 1, 'ANTM', 1156039)")
    return conn


def test_symbol_map_resolves_aliases_and_drops_fated(tmp_path):
    assert symbol_map(_db(tmp_path)) == {"ANTM": "ELV", "ELV": "ELV"}


def test_membership_joins_old_and_new_ticker(tmp_path):
    conn = _db(tmp_path)
    dates = pd.DatetimeIndex(pd.to_datetime(["2020-01-02", "2022-06-30", "2022-07-01", "2026-01-02"]))
    m = membership_panel(conn, dates, symbol_map(conn))
    assert list(m.columns) == ["ELV"]
    assert m["ELV"].tolist() == [True, True, True, True]


def test_liquidity_needs_a_full_window():
    idx = pd.bdate_range("2024-01-01", periods=5)
    close = pd.DataFrame({"A": [10.0, 10, None, 10, 10]}, index=idx)
    volume = pd.DataFrame({"A": [3e6] * 5}, index=idx)
    ok = liquidity_panel(close, volume, RULES)
    assert ok["A"].tolist() == [False, False, False, False, False]  # the gap breaks every window
    close.iloc[2] = 10.0
    assert liquidity_panel(close, volume, RULES)["A"].tolist() == [False, False, True, True, True]


def test_entities_carry_cik_sector_and_reference_etf(tmp_path):
    e = load_entities(_db(tmp_path), ["ELV", "NOPE"])
    assert e.loc["ELV", "cik"] == 1156039
    assert e.loc["ELV", "industry"] == "sic3:632"
    assert e.loc["ELV", "ref_etf"] == "XLV"
    assert bool(e.loc["ELV", "identity_verified"]) is True    # verified through ANTM's snapshot
    assert pd.isna(e.loc["NOPE", "cik"]) and e.loc["NOPE", "ref_etf"] == "SPY"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/panel/test_universe.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.panel.universe'`.

- [ ] **Step 3: Add `sic_sector` to `industry.py`**

Append to `src/analyzer/universe/industry.py`:

```python
def sic_sector(sic: str | None) -> str | None:
    """The GICS-style sector name a SIC code maps to, or None."""
    return _from_sic(sic)[0]
```

- [ ] **Step 4: Write `universe.py`**

`src/analyzer/panel/universe.py`:

```python
"""Who is in the universe on each date, and static facts about each price symbol."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from analyzer.config import UniverseRules
from analyzer.universe.industry import sic_sector


def symbol_map(conn) -> dict[str, str]:
    """Every ticker ever in an index -> the symbol its prices are stored under. Tickers with a
    fate in member_fates (no prices, or the ticker now belongs to another company) are left out."""
    aliases = dict(conn.execute("SELECT ticker, price_symbol FROM ticker_aliases").fetchall())
    fated = {r[0] for r in conn.execute("SELECT ticker FROM member_fates")}
    priced = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices")}
    out: dict[str, str] = {}
    for (ticker,) in conn.execute("SELECT DISTINCT ticker FROM index_membership"):
        if ticker in fated:
            continue
        symbol = aliases.get(ticker, ticker)
        if symbol in priced:
            out[ticker] = symbol
    return out


def membership_panel(conn, dates: pd.DatetimeIndex, symbols_of: Mapping[str, str]) -> pd.DataFrame:
    cols = sorted(set(symbols_of.values()))
    pos = {s: i for i, s in enumerate(cols)}
    arr = np.zeros((len(dates), len(cols)), dtype=bool)
    for ticker, start, end in conn.execute(
            "SELECT ticker, start_date, end_date FROM index_membership"):
        symbol = symbols_of.get(ticker)
        if symbol is None:
            continue
        i0 = dates.searchsorted(pd.Timestamp(start))
        i1 = len(dates) if end is None else dates.searchsorted(pd.Timestamp(end))
        arr[i0:i1, pos[symbol]] = True
    return pd.DataFrame(arr, index=dates, columns=cols)


def liquidity_panel(close: pd.DataFrame, volume: pd.DataFrame, rules: UniverseRules) -> pd.DataFrame:
    """Average dollar volume over a full window of bars at least the minimum. A missing bar
    inside the window (delisted, data gap) makes the window incomplete, so the day fails."""
    n = rules.dollar_volume_days
    avg = (close * volume).rolling(n, min_periods=n).mean()
    return avg >= rules.min_dollar_volume


def load_entities(conn, symbols: Sequence[str]) -> pd.DataFrame:
    tickers = pd.read_sql_query(
        "SELECT t.ticker AS symbol, t.cik, t.gics_sector, t.sector_etf, t.industry_etf, c.sic "
        "FROM tickers t LEFT JOIN companies c ON c.cik = t.cik", conn).set_index("symbol")
    alias_cik = dict(conn.execute(
        "SELECT price_symbol, cik FROM ticker_aliases WHERE cik IS NOT NULL").fetchall())
    verified = {r[0] for r in conn.execute(
        "SELECT DISTINCT t.ticker FROM tickers t JOIN index_snapshots s "
        "ON s.ticker = t.ticker AND s.cik = t.cik")}
    verified |= {r[0] for r in conn.execute(
        "SELECT DISTINCT a.price_symbol FROM ticker_aliases a JOIN index_snapshots s "
        "ON s.ticker = a.ticker AND s.cik = a.cik")}
    rows = []
    for s in symbols:
        t = tickers.loc[s] if s in tickers.index else None
        cik = t["cik"] if t is not None and pd.notna(t["cik"]) else alias_cik.get(s)
        sic = t["sic"] if t is not None and isinstance(t["sic"], str) and t["sic"] else None
        sector = (t["gics_sector"] if t is not None and isinstance(t["gics_sector"], str)
                  else sic_sector(sic))
        sector_etf = t["sector_etf"] if t is not None and isinstance(t["sector_etf"], str) else None
        industry_etf = (t["industry_etf"] if t is not None and isinstance(t["industry_etf"], str)
                        else None)
        rows.append({
            "symbol": s,
            "cik": float(cik) if cik is not None else np.nan,
            "sic": sic,
            "sector": sector,
            "industry": f"sic3:{sic[:3]}" if sic and sic.isdigit() else None,
            "sector_etf": sector_etf,
            "industry_etf": industry_etf,
            "ref_etf": industry_etf or sector_etf or "SPY",
            "identity_verified": s in verified,
        })
    return pd.DataFrame(rows).set_index("symbol")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/panel/test_universe.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/panel/universe.py src/analyzer/universe/industry.py tests/panel/test_universe.py
git commit -m "feat: membership, liquidity and entity panels with alias resolution"
```

---

### Task 7: Point-in-time step series (pure)

**Files:**
- Create: `src/analyzer/panel/steps.py`
- Test: `tests/panel/test_steps.py`

**Interfaces:**
- Consumes: `QUARTER_DAYS` from `analyzer.data.edgar.quarters`; frames shaped like `discrete_quarters` output (`period_start, period_end, value, filed, days, derived`) and like `load_concept(..., all_versions=True)` output (`cik, tag, period_start, period_end, value, filed, accession`).
- Produces (all return `DataFrame`s with `datetime64[ns]` date columns, sorted by `filed`):
  - `quarter_steps(quarters, fallback_ends=frozenset()) -> [filed, period_end, ttm, q0, q0_ly, q1, q1_ly, fallback]`;
  - `annual_steps(facts, fallback_ends=frozenset()) -> [filed, period_end, ttm, fallback]`;
  - `combine_ttm(qsteps, asteps) -> [filed, value_filed, period_end, ttm, q0, q0_ly, q1, q1_ly, fallback, annual]`;
  - `instant_steps(facts) -> [filed, period_end, value, avg5]`;
  - `latest_value_steps(facts) -> [filed, period_end, value]`;
  - `total_debt(long_term, short_term) -> facts-shaped frame [period_start, period_end, value, filed]`;
  - `sum_periods(a, b) -> [period_start, period_end, value, filed, days, derived]`;
  - `add_missing_periods(primary, extra) -> same columns as primary`;
  - `fallback_ends(facts, fallback_tags) -> frozenset[pd.Timestamp]`.

- [ ] **Step 1: Write the failing tests**

`tests/panel/test_steps.py`:

```python
import math

import pandas as pd

from analyzer.panel.steps import (
    add_missing_periods,
    annual_steps,
    combine_ttm,
    fallback_ends,
    instant_steps,
    quarter_steps,
    sum_periods,
    total_debt,
)

T = pd.Timestamp


def _q(rows):
    """rows: (start, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    df["days"] = (df.period_end - df.period_start).dt.days + 1
    df["derived"] = False
    return df


def _facts(rows, tag="us-gaap:X"):
    """rows: (start or None, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    df["cik"], df["tag"], df["accession"] = 1, tag, [f"a{i}" for i in range(len(df))]
    return df


QUARTERS = _q([
    ("2023-01-01", "2023-03-31", 10, "2023-05-01"), ("2023-04-01", "2023-06-30", 11, "2023-08-01"),
    ("2023-07-01", "2023-09-30", 12, "2023-11-01"), ("2023-10-01", "2023-12-31", 13, "2024-02-15"),
    ("2024-01-01", "2024-03-31", 20, "2024-05-01"),
])


def test_quarter_steps_sum_four_consecutive_quarters():
    s = quarter_steps(QUARTERS).set_index("filed")
    assert math.isnan(s.loc[T("2023-11-01"), "ttm"])           # only three quarters known
    assert s.loc[T("2024-02-15"), "ttm"] == 46
    assert s.loc[T("2024-05-01"), "ttm"] == 56
    assert s.loc[T("2024-05-01"), "q0"] == 20 and s.loc[T("2024-05-01"), "q0_ly"] == 10
    assert s.loc[T("2024-05-01"), "q1"] == 13 and math.isnan(s.loc[T("2024-05-01"), "q1_ly"])


def test_quarter_steps_need_consecutive_quarters():
    gap = QUARTERS.drop(index=1)
    s = quarter_steps(gap).set_index("filed")
    assert math.isnan(s.loc[T("2024-05-01"), "ttm"])


def test_quarter_steps_flag_fallback_quarters():
    s = quarter_steps(QUARTERS, frozenset({T("2023-06-30")})).set_index("filed")
    assert s.loc[T("2024-02-15"), "fallback"]
    assert s.loc[T("2024-05-01"), "fallback"]          # 2023-06-30 is still in the last four


def test_annual_steps_keep_the_first_report():
    f = _facts([("2022-01-01", "2022-12-31", 100, "2023-02-20"),
                ("2022-01-01", "2022-12-31", 90, "2024-02-20"),      # restated a year later
                ("2023-01-01", "2023-12-31", 120, "2024-02-20")])
    s = annual_steps(f).set_index("filed")
    assert s.loc[T("2023-02-20"), "ttm"] == 100
    assert s.loc[T("2024-02-20"), "ttm"] == 120 and s.loc[T("2024-02-20"), "period_end"] == T("2023-12-31")


def test_combine_prefers_newer_annual_over_stale_quarters():
    q = quarter_steps(QUARTERS.iloc[:4])                         # TTM to 2023-12-31
    a = annual_steps(_facts([("2024-01-01", "2024-12-31", 500, "2025-02-20")]))
    c = combine_ttm(q, a).set_index("filed")
    assert c.loc[T("2024-02-15"), "ttm"] == 46 and not c.loc[T("2024-02-15"), "annual"]
    assert c.loc[T("2025-02-20"), "ttm"] == 500 and c.loc[T("2025-02-20"), "annual"]
    assert c.loc[T("2025-02-20"), "value_filed"] == T("2025-02-20")


def test_combine_keeps_the_filed_date_of_the_value_used():
    q = quarter_steps(QUARTERS.iloc[:4])
    a = annual_steps(_facts([("2022-01-01", "2022-12-31", 40, "2023-02-01")]))
    c = combine_ttm(q, a).set_index("filed")
    assert c.loc[T("2023-11-01"), "annual"]                      # quarters incomplete: annual
    assert c.loc[T("2023-11-01"), "value_filed"] == T("2023-02-01")


def test_instant_steps_average_recent_period_ends():
    f = _facts([(None, "2023-03-31", 10, "2023-05-01"), (None, "2023-06-30", 20, "2023-08-01"),
                (None, "2023-06-30", 99, "2024-08-01")])          # later restatement ignored
    s = instant_steps(f).set_index("filed")
    assert s.loc[T("2023-08-01"), "value"] == 20 and s.loc[T("2023-08-01"), "avg5"] == 15
    assert T("2024-08-01") not in s.index


def test_total_debt_counts_the_current_portion_once():
    ltd = _facts([(None, "2023-12-31", 100, "2024-02-01")], tag="us-gaap:LongTermDebt")
    std = _facts([(None, "2023-12-31", 10, "2024-02-01")], tag="us-gaap:LongTermDebtCurrent")
    assert total_debt(ltd, std)["value"].tolist() == [100]
    cp = _facts([(None, "2023-12-31", 5, "2024-02-01")], tag="us-gaap:CommercialPaper")
    assert total_debt(ltd, cp)["value"].tolist() == [105]
    noncurrent = _facts([(None, "2023-12-31", 90, "2024-02-01")], tag="us-gaap:LongTermDebtNoncurrent")
    assert total_debt(noncurrent, std)["value"].tolist() == [100]


def test_bank_revenue_fills_only_missing_periods():
    nii = _q([("2023-01-01", "2023-03-31", 7, "2023-05-01"), ("2023-04-01", "2023-06-30", 7, "2023-08-01")])
    non = _q([("2023-01-01", "2023-03-31", 3, "2023-05-01"), ("2023-04-01", "2023-06-30", 3, "2023-08-02")])
    bank = sum_periods(nii, non)
    assert bank["value"].tolist() == [10, 10]
    assert bank["filed"].tolist() == [T("2023-05-01"), T("2023-08-02")]
    rev = _q([("2023-01-01", "2023-03-31", 11, "2023-05-01")])
    merged = add_missing_periods(rev, bank)
    assert merged.sort_values("period_end")["value"].tolist() == [11, 10]


def test_fallback_ends_detects_summed_rows():
    f = _facts([("2023-01-01", "2023-03-31", 1, "2023-05-01")], tag="us-gaap:A+us-gaap:B")
    assert fallback_ends(f, ()) == frozenset({T("2023-03-31")})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/panel/test_steps.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.panel.steps'`.

- [ ] **Step 3: Write `steps.py`**

`src/analyzer/panel/steps.py`:

```python
"""Point-in-time step series: one row per filing date, holding what was known after it.

Every value enters on its `filed` date. A quarter's value is the version `discrete_quarters`
keeps (the first report, or an amendment within a year), so it enters no earlier than that
version's filing. Annual and balance-sheet values keep their first report; later restatements
are ignored, which is conservative: nothing is ever known before it was filed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

from analyzer.data.edgar.quarters import QUARTER_DAYS, SAME_QUARTER_DAYS

ANNUAL_DAYS = (350, 380)
YEAR_TOLERANCE_DAYS = 20      # "the same quarter a year earlier" ends within this of end - 365
AVG_WINDOW_DAYS = 400         # balance-sheet average: period ends at most this before the latest
TOTAL_DEBT_TAG = "us-gaap:LongTermDebt"   # includes the current portion
SEPARATE_SHORT_TAGS = {"us-gaap:ShortTermBorrowings", "us-gaap:CommercialPaper"}
_DT = "datetime64[ns]"

QUARTER_COLUMNS = ["filed", "period_end", "ttm", "q0", "q0_ly", "q1", "q1_ly", "fallback"]
ANNUAL_COLUMNS = ["filed", "period_end", "ttm", "fallback"]
TTM_COLUMNS = ["filed", "value_filed", "period_end", "ttm", "q0", "q0_ly", "q1", "q1_ly",
               "fallback", "annual"]
INSTANT_COLUMNS = ["filed", "period_end", "value", "avg5"]
LATEST_COLUMNS = ["filed", "period_end", "value"]
PERIOD_COLUMNS = ["period_start", "period_end", "value", "filed", "days", "derived"]


def _empty(columns: list[str]) -> pd.DataFrame:
    dates = {"filed", "value_filed", "period_end", "period_start"}
    bools = {"fallback", "annual", "derived"}
    return pd.DataFrame({c: pd.Series(dtype=_DT if c in dates else bool if c in bools
                                      else "int64" if c == "days" else "float64")
                         for c in columns})


def _consecutive(ends: list[pd.Timestamp]) -> bool:
    return all(QUARTER_DAYS[0] <= (b - a).days <= QUARTER_DAYS[1]
               for a, b in zip(ends, ends[1:], strict=False))


def _year_ago(known: dict[pd.Timestamp, float], end: pd.Timestamp | None) -> float:
    if end is None:
        return math.nan
    target = end - pd.Timedelta(days=365)
    best = min(known, key=lambda e: abs((e - target).days))
    return known[best] if abs((best - target).days) <= YEAR_TOLERANCE_DAYS else math.nan


def quarter_steps(quarters: pd.DataFrame,
                  fallback_ends: frozenset = frozenset()) -> pd.DataFrame:
    if quarters.empty:
        return _empty(QUARTER_COLUMNS)
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in quarters.sort_values(["filed", "period_end"]).groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        ends = sorted(known)
        last4 = ends[-4:]
        complete = len(last4) == 4 and _consecutive(last4)
        q0 = ends[-1]
        q1 = ends[-2] if len(ends) >= 2 and _consecutive(ends[-2:]) else None
        rows.append((filed, q0, sum(known[e] for e in last4) if complete else math.nan,
                     known[q0], _year_ago(known, q0),
                     known[q1] if q1 is not None else math.nan, _year_ago(known, q1),
                     any(e in fallback_ends for e in last4)))
    return pd.DataFrame(rows, columns=QUARTER_COLUMNS)


def annual_steps(facts: pd.DataFrame, fallback_ends: frozenset = frozenset()) -> pd.DataFrame:
    d = facts.dropna(subset=["period_start"])
    if d.empty:
        return _empty(ANNUAL_COLUMNS)
    days = (d["period_end"] - d["period_start"]).dt.days + 1
    d = d[(days >= ANNUAL_DAYS[0]) & (days <= ANNUAL_DAYS[1])]
    d = d.sort_values(["filed", "accession"]).drop_duplicates("period_end", keep="first")
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        rows.append((filed, last, known[last], last in fallback_ends))
    return pd.DataFrame(rows, columns=ANNUAL_COLUMNS) if rows else _empty(ANNUAL_COLUMNS)


def combine_ttm(qsteps: pd.DataFrame, asteps: pd.DataFrame) -> pd.DataFrame:
    """At every filing date of either series: the quarterly TTM when its latest quarter ends no
    earlier than the latest fiscal year known, otherwise the annual value (`annual` = True).
    `value_filed` is when the value used was filed, for the age limit."""
    filed = sorted(set(qsteps["filed"]) | set(asteps["filed"]))
    if not filed:
        return _empty(TTM_COLUMNS)
    base = pd.DataFrame({"filed": pd.to_datetime(filed).astype(_DT)})
    q = qsteps.assign(q_filed=qsteps["filed"]).sort_values("filed")
    a = (asteps.rename(columns={"period_end": "a_end", "ttm": "a_ttm", "fallback": "a_fallback"})
         .assign(a_filed=asteps["filed"]).sort_values("filed"))
    qq = pd.merge_asof(base, q, on="filed") if len(q) else base.assign(
        **{c: np.nan for c in QUARTER_COLUMNS[1:]}, q_filed=pd.NaT)
    aa = pd.merge_asof(base, a, on="filed") if len(a) else base.assign(
        a_end=pd.NaT, a_ttm=np.nan, a_fallback=False, a_filed=pd.NaT)
    has_annual = aa["a_ttm"].notna()
    newer_annual = aa["a_end"].notna() & (aa["a_end"] > pd.to_datetime(qq["period_end"]))
    use_annual = has_annual & (qq["ttm"].isna() | newer_annual)
    out = pd.DataFrame({
        "filed": base["filed"],
        "value_filed": pd.to_datetime(np.where(use_annual, aa["a_filed"], qq["q_filed"])).astype(_DT),
        "period_end": pd.to_datetime(np.where(use_annual, aa["a_end"], qq["period_end"])).astype(_DT),
        "ttm": np.where(use_annual, aa["a_ttm"], qq["ttm"]).astype("float64"),
        "q0": qq["q0"].astype("float64"), "q0_ly": qq["q0_ly"].astype("float64"),
        "q1": qq["q1"].astype("float64"), "q1_ly": qq["q1_ly"].astype("float64"),
        "fallback": np.where(use_annual, aa["a_fallback"].astype("boolean").fillna(False),
                             qq["fallback"].astype("boolean").fillna(False)).astype(bool),
        "annual": use_annual.to_numpy(dtype=bool),
    })
    return out[TTM_COLUMNS]


def _first_reports(facts: pd.DataFrame) -> pd.DataFrame:
    """Instant facts, one per balance-sheet date: the first filed version."""
    d = facts[facts["period_start"].isna()]
    sort = ["filed", "accession"] if "accession" in d else ["filed"]
    return d.sort_values(sort).drop_duplicates("period_end", keep="first")


def instant_steps(facts: pd.DataFrame) -> pd.DataFrame:
    d = _first_reports(facts)
    if d.empty:
        return _empty(INSTANT_COLUMNS)
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        recent = [e for e in sorted(known)[-5:] if (last - e).days <= AVG_WINDOW_DAYS]
        rows.append((filed, last, known[last], float(np.mean([known[e] for e in recent]))))
    return pd.DataFrame(rows, columns=INSTANT_COLUMNS)


def latest_value_steps(facts: pd.DataFrame) -> pd.DataFrame:
    """Duration facts of any length: the value with the latest period end known at each filing.
    Used for weighted-average diluted shares."""
    if facts.empty:
        return _empty(LATEST_COLUMNS)
    d = facts.sort_values("filed").drop_duplicates(["period_start", "period_end"], keep="first")
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        rows.append((filed, last, known[last]))
    return pd.DataFrame(rows, columns=LATEST_COLUMNS)


def total_debt(long_term: pd.DataFrame, short_term: pd.DataFrame) -> pd.DataFrame:
    """Debt per balance-sheet date. `LongTermDebt` already includes the current portion, so a
    short-term line is added to it only when it is borrowings or commercial paper."""
    cols = ["period_end", "value", "filed", "tag"]
    lt = _first_reports(long_term)[cols] if len(long_term) else pd.DataFrame(columns=cols)
    st = _first_reports(short_term)[cols] if len(short_term) else pd.DataFrame(columns=cols)
    if lt.empty and st.empty:
        return pd.DataFrame({"period_start": pd.Series(dtype=_DT), "period_end": pd.Series(dtype=_DT),
                             "value": pd.Series(dtype="float64"), "filed": pd.Series(dtype=_DT)})
    m = lt.merge(st, on="period_end", how="outer", suffixes=("_l", "_s"))
    overlap = (m["tag_l"] == TOTAL_DEBT_TAG) & ~m["tag_s"].isin(SEPARATE_SHORT_TAGS)
    short = m["value_s"].astype("float64").fillna(0).where(~overlap, 0.0)
    value = m["value_l"].astype("float64").fillna(0) + short
    filed = pd.concat([pd.to_datetime(m["filed_l"]), pd.to_datetime(m["filed_s"])], axis=1).max(axis=1)
    return pd.DataFrame({"period_start": pd.Series(pd.NaT, index=m.index, dtype=_DT),
                         "period_end": pd.to_datetime(m["period_end"]).astype(_DT),
                         "value": value, "filed": filed.astype(_DT)})


def sum_periods(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Period-by-period sum of two duration series (bank revenue = NII + noninterest income),
    known once both parts are filed."""
    if a.empty or b.empty:
        return _empty(PERIOD_COLUMNS)
    m = a.merge(b, on=["period_start", "period_end"], suffixes=("_a", "_b"))
    out = pd.DataFrame({
        "period_start": m["period_start"], "period_end": m["period_end"],
        "value": m["value_a"] + m["value_b"],
        "filed": m[["filed_a", "filed_b"]].max(axis=1).astype(_DT),
    })
    out["days"] = (out["period_end"] - out["period_start"]).dt.days + 1
    out["derived"] = (m["derived_a"] | m["derived_b"]) if "derived_a" in m else False
    return out[PERIOD_COLUMNS]


def add_missing_periods(primary: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """`primary` plus the rows of `extra` whose period is not already in it."""
    if extra.empty:
        return primary
    ends = primary["period_end"].to_numpy()
    keep = [not np.any(np.abs((ends - e) / np.timedelta64(1, "D")) <= SAME_QUARTER_DAYS)
            for e in extra["period_end"].to_numpy()]
    return pd.concat([primary, extra[keep][primary.columns]], ignore_index=True)


def fallback_ends(facts: pd.DataFrame, fallback_tags: Iterable[str]) -> frozenset:
    """Period ends whose value came from a summed fallback row (companyfacts._fallback_rows)."""
    tags = set(fallback_tags)
    mask = facts["tag"].str.contains("+", regex=False) | facts["tag"].isin(tags)
    return frozenset(facts.loc[mask, "period_end"])
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/panel/test_steps.py -v`
Expected: all PASS. If `test_bank_revenue_fills_only_missing_periods` fails on `primary.columns` because `rev` lacks a column, check that `_q` builds the same columns as `discrete_quarters` (`period_start, period_end, value, filed, days, derived`).

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/panel/steps.py tests/panel/test_steps.py
git commit -m "feat: point-in-time step series for TTM, annual, balance-sheet and debt values"
```

---

### Task 8: Daily fundamental panels

**Files:**
- Create: `src/analyzer/panel/fundamentals.py`
- Test: `tests/panel/test_fundamentals.py`

**Interfaces:**
- Consumes: `load_concept` (`facts_read.py`), `discrete_quarters` (`quarters.py`), `CONCEPT_BY_NAME` (`tags.py`), everything from Task 7, `load_cik_merges` (`merges.py`), `FeatureRules`.
- Produces:
  - `FIELDS` = `("revenue_ttm", "operating_income_ttm", "net_income_ttm", "eps_ttm", "da_ttm", "ocf_ttm", "capex_ttm", "revenue_q0", "revenue_q0_ly", "revenue_q1", "revenue_q1_ly", "equity", "equity_avg", "cash", "debt", "shares")`; `FLAGS` = `("annual", "fallback", "diluted_shares")`;
  - `FundamentalPanels` (frozen dataclass): `frames: dict[str, DataFrame]` (every name in `FIELDS` as `float32`, every name in `FLAGS` as `bool`, each indexed by trading date with one column per symbol), `first_filed: Series[Timestamp]`, `has_property: Series[bool]`, `has_us_gaap: Series[bool]` (all three indexed by symbol); `__getitem__(name)`; `upto(end)`;
  - `company_steps(conn, cik, merges=None) -> dict[str, DataFrame]`;
  - `load_fundamental_panels(conn, entities, dates, rules: FeatureRules) -> FundamentalPanels`;
  - `load_splits(conn) -> dict[str, list[tuple[pd.Timestamp, float]]]`;
  - `merges_from_tickers(conn) -> dict[int, list[int]]`.

- [ ] **Step 1: Write the failing tests**

`tests/panel/test_fundamentals.py`:

```python
import math

import pandas as pd
import pytest

from analyzer.config import load_rules
from analyzer.panel.fundamentals import load_fundamental_panels
from analyzer.store.db import connect, migrate

RULES = load_rules().features


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    return conn


def _fact(conn, concept, tag, start, end, value, filed, accn=None):
    conn.execute("INSERT INTO facts VALUES (1, ?, ?, 'USD', ?, ?, ?, ?, '10-Q', ?, 'companyfacts')",
                 (concept, tag, start or "", end, value, filed, accn or f"{concept}-{end}-{filed}"))


def _quarters(conn, concept, tag, values, first_filed="2020-05-01"):
    ends = pd.date_range("2020-03-31", periods=len(values), freq="QE")
    filed = pd.date_range(first_filed, periods=len(values), freq="QS-FEB") + pd.Timedelta(days=0)
    for end, f, v in zip(ends, filed, values, strict=True):
        start = (end - pd.offsets.QuarterEnd() + pd.Timedelta(days=1)).date().isoformat()
        _fact(conn, concept, tag, start, end.date().isoformat(), v, f.date().isoformat())


def _entities():
    return pd.DataFrame({"cik": [1.0]}, index=pd.Index(["AAA"], name="symbol"))


def test_value_appears_on_its_filed_date(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 10, 10, 10])
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    rev = f["revenue_ttm"]["AAA"]
    last_filed = rev.first_valid_index()
    assert rev.loc[last_filed] == 40
    assert rev.loc[:last_filed].iloc[:-1].isna().all()


def test_ttm_goes_stale_after_max_age(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 10, 10, 10])
    dates = pd.bdate_range("2020-01-01", "2023-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    rev = f["revenue_ttm"]["AAA"]
    first = rev.first_valid_index()
    assert rev.loc[first + pd.Timedelta(days=RULES.ttm_max_age_days - 30):].iloc[0] == 40
    assert rev.loc[first + pd.Timedelta(days=RULES.ttm_max_age_days + 30):].isna().all()


def test_market_cap_is_continuous_across_split(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", None,
          "2024-03-31", 100.0, "2024-05-01")
    conn.execute("INSERT INTO splits VALUES ('AAA', '2024-06-10', 2.0)")  # 2-for-1 after the count
    dates = pd.bdate_range("2024-05-01", "2024-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    # Stored closes are on the post-split basis everywhere, so shares must be too: 200.
    assert f["shares"]["AAA"].dropna().unique().tolist() == [200.0]


def test_cover_shares_fall_back_to_diluted_when_stale(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", None,
          "2020-03-31", 100.0, "2020-05-01")
    _fact(conn, "diluted_shares", "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
          "2022-01-01", "2022-03-31", 120.0, "2022-05-01")
    dates = pd.bdate_range("2022-05-02", "2022-05-06")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["shares"]["AAA"].tolist() == [120.0] * 5
    assert f["diluted_shares"]["AAA"].all()


def test_bank_revenue_fills_missing_revenue(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "net_interest_income", "us-gaap:InterestIncomeExpenseNet", [7, 7, 7, 7])
    _quarters(conn, "noninterest_income", "us-gaap:NoninterestIncome", [3, 3, 3, 3])
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["revenue_ttm"]["AAA"].dropna().iloc[-1] == 40


def test_negative_derived_quarter_is_dropped(tmp_path):
    conn = _db(tmp_path)
    # 9M YTD 300, FY 280: a derived Q4 of -20 is impossible for revenue.
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-03-31", 100, "2020-05-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-06-30", 200, "2020-08-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-09-30", 300, "2020-11-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-12-31", 280, "2021-02-15")
    dates = pd.bdate_range("2021-02-15", "2021-02-19")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["revenue_q0"]["AAA"].iloc[0] == 100          # Q3; the negative Q4 is gone
    assert f["revenue_ttm"]["AAA"].iloc[0] == 280         # annual fallback
    assert f["annual"]["AAA"].iloc[0]


def test_static_series(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10])
    _fact(conn, "real_estate_property", "us-gaap:RealEstateInvestmentPropertyNet", None,
          "2020-03-31", 5.0, "2020-05-01")
    f = load_fundamental_panels(conn, _entities(), pd.bdate_range("2020-01-01", periods=5), RULES)
    assert f.has_property["AAA"] and f.has_us_gaap["AAA"]
    assert f.first_filed["AAA"] == pd.Timestamp("2020-05-01")
```

Before running, check `_quarters`' filing dates: `pd.date_range(first_filed, periods=n, freq="QS-FEB")` gives 2020-05-01, 2020-08-01, 2020-11-01, 2021-02-01 — one filing about a month after each quarter end. If the pandas version rejects `"QE"`, use `"Q"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/panel/test_fundamentals.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.panel.fundamentals'`.

- [ ] **Step 3: Write `fundamentals.py`**

`src/analyzer/panel/fundamentals.py`:

```python
"""Daily fundamental panels: every company's step series, as-of joined onto trading dates."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.data.edgar.facts_read import load_concept
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.quarters import discrete_quarters
from analyzer.data.edgar.tags import CONCEPT_BY_NAME
from analyzer.panel.steps import (
    add_missing_periods,
    annual_steps,
    combine_ttm,
    fallback_ends,
    instant_steps,
    latest_value_steps,
    quarter_steps,
    sum_periods,
    total_debt,
)

log = logging.getLogger(__name__)

DURATIONS = {  # panel prefix -> concept
    "revenue": "revenue", "operating_income": "operating_income", "net_income": "net_income",
    "eps": "eps_diluted", "da": "depreciation_amortization", "ocf": "operating_cash_flow",
    "capex": "capex",
}
NON_NEGATIVE = {"revenue", "capex", "depreciation_amortization"}
FLAGGED = ("revenue", "operating_income", "net_income", "ocf", "capex")  # feed annual/fallback
QUARTER_FIELDS = ("revenue_q0", "revenue_q0_ly", "revenue_q1", "revenue_q1_ly")
FIELDS = (*(f"{p}_ttm" for p in DURATIONS), *QUARTER_FIELDS,
          "equity", "equity_avg", "cash", "debt", "shares")
FLAGS = ("annual", "fallback", "diluted_shares")


@dataclass(frozen=True)
class FundamentalPanels:
    frames: dict[str, pd.DataFrame]
    first_filed: pd.Series
    has_property: pd.Series
    has_us_gaap: pd.Series

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.frames[name]

    def upto(self, end) -> FundamentalPanels:
        end = pd.Timestamp(end)
        return FundamentalPanels({k: v.loc[:end] for k, v in self.frames.items()},
                                 self.first_filed, self.has_property, self.has_us_gaap)


def merges_from_tickers(conn) -> dict[int, list[int]]:
    tm = pd.read_sql_query("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL", conn)
    return load_cik_merges(tm)


def load_splits(conn) -> dict[str, list[tuple[pd.Timestamp, float]]]:
    out: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    for symbol, d, ratio in conn.execute("SELECT symbol, date, ratio FROM splits WHERE ratio > 0"):
        out.setdefault(symbol, []).append((pd.Timestamp(d), float(ratio)))
    return out


def company_steps(conn, cik: int, merges: Mapping[int, Sequence[int]] | None = None
                  ) -> dict[str, pd.DataFrame]:
    def facts(concept: str) -> pd.DataFrame:
        return load_concept(conn, cik, concept, merges=merges, all_versions=True)

    out: dict[str, pd.DataFrame] = {}
    for prefix, concept in DURATIONS.items():
        f = facts(concept)
        q = discrete_quarters(f)
        if concept in NON_NEGATIVE:
            q = q[~(q["derived"] & (q["value"] < 0))]
        annual_facts = f
        if concept == "revenue":  # banks: NII + noninterest income where no revenue tag exists
            nii, non = facts("net_interest_income"), facts("noninterest_income")
            q = add_missing_periods(q, sum_periods(discrete_quarters(nii), discrete_quarters(non)))
            bank_annual = sum_periods(nii.assign(days=0, derived=False),
                                      non.assign(days=0, derived=False))
            if len(bank_annual):
                bank_annual = bank_annual.assign(tag="bank", accession="bank", cik=cik)
                annual_facts = add_missing_periods(f, bank_annual) if len(f) else bank_annual
        fb = fallback_ends(f, CONCEPT_BY_NAME[concept].fallback_tags) if len(f) else frozenset()
        out[prefix] = combine_ttm(quarter_steps(q, fb), annual_steps(annual_facts, fb))
    out["equity"] = instant_steps(facts("stockholders_equity"))
    out["cash"] = instant_steps(facts("cash"))
    out["debt"] = instant_steps(total_debt(facts("long_term_debt"), facts("short_term_debt")))
    out["shares_cover"] = instant_steps(facts("shares_outstanding"))
    out["shares_diluted"] = latest_value_steps(facts("diluted_shares"))
    return out


def _asof(steps: pd.DataFrame, dates: pd.DatetimeIndex, column: str) -> tuple[np.ndarray, np.ndarray]:
    """(values, index of the step used or -1) for each date: the last step filed on or before it."""
    if steps.empty:
        return np.full(len(dates), np.nan), np.full(len(dates), -1)
    filed = steps["filed"].to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(filed, dates.to_numpy(dtype="datetime64[ns]"), side="right") - 1
    values = steps[column].to_numpy(dtype="float64")[idx.clip(0)]
    values[idx < 0] = np.nan
    return values, idx


def _fresh(steps: pd.DataFrame, idx: np.ndarray, dates: pd.DatetimeIndex, column: str,
           max_age_days: int) -> np.ndarray:
    if steps.empty:
        return np.zeros(len(dates), dtype=bool)
    when = steps[column].to_numpy(dtype="datetime64[ns]")[idx.clip(0)]
    age = (dates.to_numpy(dtype="datetime64[ns]") - when) / np.timedelta64(1, "D")
    return (idx >= 0) & (age <= max_age_days)


def _split_adjusted(steps: pd.DataFrame, splits: list[tuple[pd.Timestamp, float]]) -> pd.DataFrame:
    """Share counts are on the basis of their own date; stored closes are on the basis of the
    latest download. Multiply each count by every split after its date (see market_cap.py)."""
    if steps.empty or not splits:
        return steps
    factor = [float(np.prod([r for d, r in splits if d > end] or [1.0])) for end in steps["period_end"]]
    return steps.assign(value=steps["value"] * np.array(factor))


def _company_columns(steps: dict[str, pd.DataFrame], dates: pd.DatetimeIndex,
                     splits: list[tuple[pd.Timestamp, float]], rules: FeatureRules
                     ) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    annual = np.zeros(len(dates), dtype=bool)
    fallback = np.zeros(len(dates), dtype=bool)
    for prefix in DURATIONS:
        s = steps[prefix]
        values, idx = _asof(s, dates, "ttm")
        fresh = _fresh(s, idx, dates, "value_filed", rules.ttm_max_age_days)
        cols[f"{prefix}_ttm"] = np.where(fresh, values, np.nan)
        if prefix == "revenue":
            for q in ("q0", "q0_ly", "q1", "q1_ly"):
                qv, qidx = _asof(s, dates, q)
                cols[f"revenue_{q}"] = np.where(_fresh(s, qidx, dates, "filed", rules.ttm_max_age_days),
                                                qv, np.nan)
        if prefix in FLAGGED and not s.empty:
            a, _ = _asof(s.assign(annual=s["annual"].astype(float)), dates, "annual")
            fb, _ = _asof(s.assign(fallback=s["fallback"].astype(float)), dates, "fallback")
            annual |= fresh & (a == 1)
            fallback |= fresh & (fb == 1)
    for name in ("equity", "cash", "debt"):
        s = steps[name]
        values, idx = _asof(s, dates, "value")
        cols[name] = np.where(_fresh(s, idx, dates, "filed", rules.ttm_max_age_days), values, np.nan)
        if name == "equity":
            avg, _ = _asof(s, dates, "avg5")
            cols["equity_avg"] = np.where(np.isnan(cols["equity"]), np.nan, avg)
    cover = _split_adjusted(steps["shares_cover"], splits)
    diluted = _split_adjusted(steps["shares_diluted"], splits)
    cv, cidx = _asof(cover, dates, "value")
    dv, didx = _asof(diluted, dates, "value")
    c_ok = _fresh(cover, cidx, dates, "period_end", rules.shares_max_age_days)
    d_ok = _fresh(diluted, didx, dates, "period_end", rules.shares_max_age_days)
    cols["shares"] = np.where(c_ok, cv, np.where(d_ok, dv, np.nan))
    cols["diluted_shares"] = ~c_ok & d_ok
    cols["annual"] = annual
    cols["fallback"] = fallback
    return cols


def load_fundamental_panels(conn, entities: pd.DataFrame, dates: pd.DatetimeIndex,
                            rules: FeatureRules) -> FundamentalPanels:
    merges = merges_from_tickers(conn)
    splits = load_splits(conn)
    property_ciks = {r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE concept = 'real_estate_property'")}
    gaap_ciks = {r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE concept IN ('revenue', 'net_income') "
        "AND tag LIKE 'us-gaap:%'")}
    first = dict(conn.execute(
        "SELECT cik, MIN(filed) FROM facts WHERE concept IN ('revenue', 'net_income', "
        "'net_interest_income') GROUP BY cik").fetchall())

    symbols = list(entities.index)
    data: dict[str, dict[str, np.ndarray]] = {n: {} for n in (*FIELDS, *FLAGS)}
    first_filed, has_property, has_gaap = {}, {}, {}
    for n, symbol in enumerate(symbols, 1):
        cik = entities.at[symbol, "cik"]
        if pd.isna(cik):
            continue
        cik = int(cik)
        group = [cik, *merges.get(cik, [])]
        try:
            cols = _company_columns(company_steps(conn, cik, merges), dates,
                                    splits.get(symbol, []), rules)
        except Exception:  # one unreadable company must not stop the run; it is counted as missing
            log.exception("fundamentals for %s (CIK %d) failed", symbol, cik)
            continue
        for name, values in cols.items():
            data[name][symbol] = values
        firsts = [first[c] for c in group if c in first]
        first_filed[symbol] = pd.Timestamp(min(firsts)) if firsts else pd.NaT
        has_property[symbol] = any(c in property_ciks for c in group)
        has_gaap[symbol] = any(c in gaap_ciks for c in group)
        if n % 200 == 0:
            log.info("fundamentals: %d/%d symbols", n, len(symbols))

    frames = {}
    for name in FIELDS:
        frames[name] = pd.DataFrame(data[name], index=dates).reindex(columns=symbols).astype("float32")
    for name in FLAGS:
        frames[name] = (pd.DataFrame(data[name], index=dates).reindex(columns=symbols)
                        .fillna(False).astype(bool))
    idx = pd.Index(symbols, name="symbol")
    return FundamentalPanels(
        frames=frames,
        first_filed=pd.Series(first_filed, dtype="datetime64[ns]").reindex(idx),
        has_property=pd.Series(has_property, dtype=bool).reindex(idx, fill_value=False),
        has_us_gaap=pd.Series(has_gaap, dtype=bool).reindex(idx, fill_value=False),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/panel -v`
Expected: all PASS. Two likely snags:
- `bank_annual` in `company_steps`: `sum_periods` needs `days`/`derived` columns; the `assign(days=0, derived=False)` gives them. `annual_steps` then keeps only the ~365-day periods.
- `pd.DataFrame(data[name], index=dates)` with an empty dict gives an empty frame; `reindex(columns=symbols)` fills NaN, which is what an unknown company should be.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/panel/fundamentals.py tests/panel/test_fundamentals.py
git commit -m "feat: daily point-in-time fundamental panels with age limits and split-adjusted shares"
```

---
### Task 9: Value features

**Files:**
- Create: `src/analyzer/features/__init__.py` (empty)
- Create: `src/analyzer/features/value.py`
- Create: `tests/features/__init__.py` (empty)
- Test: `tests/features/test_value.py`

**Interfaces:**
- Consumes: `FundamentalPanels` (Task 8), `FeatureRules`; `entities` frame (Task 6) with columns `industry`, `sector`.
- Produces:
  - `METRICS = ("pe", "ev_ebitda", "p_fcf", "ps", "pb", "p_ffo")`; `VARIANT_METRICS: dict[str, tuple[str, ...]]` = `{"standard": ("pe", "ev_ebitda", "p_fcf", "ps"), "financials": ("pe", "pb"), "reit": ("p_ffo", "ev_ebitda", "ps")}`; `LABELS: dict[str, str]`;
  - `market_cap(close, shares) -> DataFrame`; `net_debt(f) -> DataFrame`;
  - `ratios(close, f) -> dict[str, DataFrame]` (NaN where the denominator is missing or ≤ 0);
  - `own_percentile(ratio, window, min_obs) -> DataFrame` (0–100);
  - `peer_median(ratio, eligible, entities, min_size) -> DataFrame`;
  - `ValueFeatures` (frozen dataclass: `ratio`, `pct`, `p50`, `peer` — each `dict[str, DataFrame]` keyed by metric, frames restricted to the symbols that use the metric — plus `market_cap: DataFrame`, `net_debt: DataFrame`, `ebitda: DataFrame`);
  - `value_features(close, f, eligible, entities, columns_for, rules) -> ValueFeatures`, where `columns_for: Mapping[str, Sequence[str]]` gives, per metric, the symbols that need it.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_value.py`:

```python
import numpy as np
import pandas as pd

from analyzer.features.value import own_percentile, peer_median, ratios
from analyzer.panel.fundamentals import FIELDS, FLAGS, FundamentalPanels
from tests.panel_fixtures import assert_truncation_invariant, cut


def _fund(index, columns, **values):
    frames = {n: pd.DataFrame(np.nan, index=index, columns=columns, dtype="float32") for n in FIELDS}
    frames.update({n: pd.DataFrame(False, index=index, columns=columns) for n in FLAGS})
    for name, v in values.items():
        frames[name] = pd.DataFrame(v, index=index, columns=columns, dtype="float32")
    empty = pd.Series(dtype=bool)
    return FundamentalPanels(frames, pd.Series(dtype="datetime64[ns]"), empty, empty)


def test_ratios_drop_non_positive_denominators():
    idx = pd.bdate_range("2024-01-01", periods=2)
    close = pd.DataFrame({"A": [10.0, 10.0]}, index=idx)
    f = _fund(idx, ["A"], shares=100.0, net_income_ttm=[[50.0], [-5.0]], revenue_ttm=200.0,
              operating_income_ttm=40.0, da_ttm=10.0, ocf_ttm=60.0, capex_ttm=20.0,
              equity=500.0, debt=300.0, cash=100.0)
    r = ratios(close, f)
    assert r["pe"]["A"].tolist()[0] == 20.0 and np.isnan(r["pe"]["A"].tolist()[1])
    assert r["ps"]["A"].iloc[0] == 5.0
    assert r["ev_ebitda"]["A"].iloc[0] == (1000 + 200) / 50
    assert r["p_fcf"]["A"].iloc[0] == 1000 / 40
    assert r["pb"]["A"].iloc[0] == 2.0
    assert r["p_ffo"]["A"].iloc[0] == 1000 / 60


def test_own_percentile_needs_min_history_and_ranks_today():
    s = pd.DataFrame({"A": np.arange(1.0, 11.0)}, index=pd.bdate_range("2024-01-01", periods=10))
    p = own_percentile(s, window=5, min_obs=3)
    assert p["A"].iloc[:2].isna().all()
    assert p["A"].iloc[-1] == 100.0
    s.iloc[-1] = 0.0
    assert own_percentile(s, 5, 3)["A"].iloc[-1] == 20.0


def test_peer_median_falls_back_to_sector_for_small_industries():
    idx = pd.bdate_range("2024-01-01", periods=1)
    cols = ["A", "B", "C", "D"]
    r = pd.DataFrame([[10.0, 20.0, 30.0, 40.0]], index=idx, columns=cols)
    eligible = pd.DataFrame(True, index=idx, columns=cols)
    ent = pd.DataFrame({"industry": ["i1", "i1", "i1", "i2"], "sector": ["s"] * 4}, index=cols)
    m = peer_median(r, eligible, ent, min_size=3)
    assert m.loc[idx[0], "A"] == 20.0        # i1 has 3 members
    assert m.loc[idx[0], "D"] == 25.0        # i2 has 1: sector median of all four


def test_peer_median_ignores_ineligible_stocks():
    idx = pd.bdate_range("2024-01-01", periods=1)
    r = pd.DataFrame([[10.0, 1000.0]], index=idx, columns=["A", "B"])
    eligible = pd.DataFrame([[True, False]], index=idx, columns=["A", "B"])
    ent = pd.DataFrame({"industry": ["i", "i"], "sector": ["s", "s"]}, index=["A", "B"])
    assert peer_median(r, eligible, ent, min_size=1).loc[idx[0], "A"] == 10.0


def test_percentile_is_truncation_invariant():
    rng = np.random.default_rng(1)
    s = pd.DataFrame(rng.normal(10, 2, size=(60, 3)), index=pd.bdate_range("2024-01-01", periods=60),
                     columns=list("ABC"))
    assert_truncation_invariant(lambda end: own_percentile(cut(s, end), 20, 10),
                                [s.index[15], s.index[40]])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_value.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.features'`.

- [ ] **Step 3: Write `value.py`**

`src/analyzer/features/value.py`:

```python
"""Value metrics (spec 5.1). Every metric is a price per unit, so lower is always cheaper:
P/E, EV/EBITDA, P/FCF (the inverse of FCF yield), P/S, P/B and P/FFO."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.panel.fundamentals import FundamentalPanels

METRICS = ("pe", "ev_ebitda", "p_fcf", "ps", "pb", "p_ffo")
VARIANT_METRICS: dict[str, tuple[str, ...]] = {
    "standard": ("pe", "ev_ebitda", "p_fcf", "ps"),
    "financials": ("pe", "pb"),
    "reit": ("p_ffo", "ev_ebitda", "ps"),
}
LABELS = {"pe": "P/E", "ev_ebitda": "EV/EBITDA", "p_fcf": "FCF yield", "ps": "P/S",
          "pb": "P/B", "p_ffo": "P/FFO"}


def market_cap(close: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    return close * shares.astype("float64")


def net_debt(f: FundamentalPanels) -> pd.DataFrame:
    """Debt minus cash. A company that reports a balance sheet but no debt line has no debt."""
    debt = f["debt"].astype("float64").fillna(0.0).where(f["equity"].notna())
    return debt - f["cash"].astype("float64").fillna(0.0)


def _per(num: pd.DataFrame, den: pd.DataFrame) -> pd.DataFrame:
    den = den.astype("float64")
    return (num / den).where(den > 0)


def ratios(close: pd.DataFrame, f: FundamentalPanels) -> dict[str, pd.DataFrame]:
    cap = market_cap(close, f["shares"])
    ev = cap + net_debt(f)
    ebitda = f["operating_income_ttm"].astype("float64") + f["da_ttm"].astype("float64")
    fcf = f["ocf_ttm"].astype("float64") - f["capex_ttm"].astype("float64")
    ffo = f["net_income_ttm"].astype("float64") + f["da_ttm"].astype("float64")
    return {
        "pe": _per(cap, f["net_income_ttm"]),
        "ev_ebitda": _per(ev, ebitda).where(ev > 0),
        "p_fcf": _per(cap, fcf),
        "ps": _per(cap, f["revenue_ttm"]),
        "pb": _per(cap, f["equity"]),
        "p_ffo": _per(cap, ffo),
    }


def own_percentile(ratio: pd.DataFrame, window: int, min_obs: int) -> pd.DataFrame:
    """Percentile (0-100) of today's value among the symbol's own last `window` daily values."""
    return ratio.rolling(window, min_periods=min_obs).rank(pct=True) * 100


def _group_stats(values: np.ndarray, keys: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Per date and group: the median over the group's columns, and how many had a value."""
    med = np.full(values.shape, np.nan)
    cnt = np.zeros(values.shape, dtype=np.int32)
    codes = keys.to_numpy(dtype=object)
    for key in pd.unique(keys.dropna()):
        cols = np.flatnonzero(codes == key)
        block = values[:, cols]
        with warnings.catch_warnings():  # an all-NaN date gives NaN, which is what we want
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmedian(block, axis=1)
        med[:, cols] = m[:, None]
        cnt[:, cols] = (~np.isnan(block)).sum(axis=1)[:, None]
    return med, cnt


def peer_median(ratio: pd.DataFrame, eligible: pd.DataFrame, entities: pd.DataFrame,
                min_size: int) -> pd.DataFrame:
    """Median of the metric over the stock's industry peers in the universe that day, or over its
    sector when the industry has fewer than `min_size` members with a value (spec 5.1)."""
    values = ratio.where(eligible.reindex_like(ratio).fillna(False).astype(bool)).to_numpy(dtype="float64")
    ind = entities["industry"].reindex(ratio.columns)
    sec = entities["sector"].reindex(ratio.columns)
    ind_med, ind_cnt = _group_stats(values, ind)
    sec_med, _ = _group_stats(values, sec)
    out = np.where(ind_cnt >= min_size, ind_med, sec_med)
    return pd.DataFrame(out, index=ratio.index, columns=ratio.columns)


@dataclass(frozen=True)
class ValueFeatures:
    ratio: dict[str, pd.DataFrame]
    pct: dict[str, pd.DataFrame]
    p50: dict[str, pd.DataFrame]
    peer: dict[str, pd.DataFrame]
    market_cap: pd.DataFrame
    net_debt: pd.DataFrame
    ebitda: pd.DataFrame


def value_features(close: pd.DataFrame, f: FundamentalPanels, eligible: pd.DataFrame,
                   entities: pd.DataFrame, columns_for: Mapping[str, Sequence[str]],
                   rules: FeatureRules) -> ValueFeatures:
    all_ratios = ratios(close, f)
    r, pct, p50, peer = {}, {}, {}, {}
    for metric in METRICS:
        cols = [c for c in columns_for.get(metric, ()) if c in close.columns]
        if not cols:
            continue
        x = all_ratios[metric][cols]
        r[metric] = x.astype("float32")
        pct[metric] = own_percentile(x, rules.percentile_window, rules.percentile_min_obs).astype("float32")
        p50[metric] = x.rolling(rules.percentile_window, min_periods=rules.percentile_min_obs
                                ).median().astype("float32")
        # Peers: every eligible stock that uses the metric, not only this variant's columns.
        peer[metric] = peer_median(x, eligible, entities, rules.peer_min_size).astype("float32")
    ebitda = f["operating_income_ttm"].astype("float64") + f["da_ttm"].astype("float64")
    return ValueFeatures(r, pct, p50, peer, market_cap(close, f["shares"]), net_debt(f), ebitda)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_value.py -v`
Expected: all PASS. If `rolling(...).rank` raises on an old pandas, upgrade is not allowed: replace it with `rolling(...).apply(lambda w: (w <= w[-1]).sum() / (~np.isnan(w)).sum(), raw=True)` and note the slowdown in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features tests/features
git commit -m "feat: value ratios, own-history percentiles and peer medians"
```

---

### Task 10: Quality features

**Files:**
- Create: `src/analyzer/features/quality.py`
- Test: `tests/features/test_quality.py`

**Interfaces:**
- Consumes: `FundamentalPanels`, `FeatureRules`, `ScoreRules`, `net_debt` from `value.py`.
- Produces: `QualityFeatures` (frozen dataclass of `DataFrame`s): `revenue_growth`, `revenue_q_growth`, `eps_growth`, `revenue_decline_2q` (bool), `margin_change`, `fcf`, `fcf_prev`, `nd_ebitda`, `roe_change`, `growth_score`, `margin_score`, `roe_score`, `fcf_score`; `quality_features(f, net_debt, features: FeatureRules, score: ScoreRules) -> QualityFeatures`; `balance_score(nd_ebitda, cap: float) -> DataFrame`.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_quality.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.quality import balance_score, quality_features
from analyzer.panel.fundamentals import FIELDS, FLAGS, FundamentalPanels
from tests.panel_fixtures import assert_truncation_invariant

RULES = load_rules()
YEAR = RULES.features.year_days


def _fund(n, **series):
    idx = pd.bdate_range("2020-01-01", periods=n)
    frames = {k: pd.DataFrame(np.nan, index=idx, columns=["A"], dtype="float32") for k in FIELDS}
    frames.update({k: pd.DataFrame(False, index=idx, columns=["A"]) for k in FLAGS})
    for k, v in series.items():
        frames[k] = pd.DataFrame({"A": v}, index=idx, dtype="float32")
    e = pd.Series(dtype=bool)
    return FundamentalPanels(frames, pd.Series(dtype="datetime64[ns]"), e, e)


def _q(f):
    from analyzer.features.value import net_debt
    return quality_features(f, net_debt(f), RULES.features, RULES.score)


def test_growth_and_margin_change_compare_with_a_year_earlier():
    n = YEAR + 1
    rev = [100.0] * YEAR + [120.0]
    op = [10.0] * YEAR + [6.0]
    q = _q(_fund(n, revenue_ttm=rev, operating_income_ttm=op))
    assert abs(q.revenue_growth["A"].iloc[-1] - 0.20) < 1e-6
    assert abs(q.margin_change["A"].iloc[-1] - (0.05 - 0.10)) < 1e-6
    assert abs(q.growth_score["A"].iloc[-1] - 1.0) < 1e-6        # +20% = full credit


def test_revenue_decline_needs_both_quarters_down():
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[11.0], revenue_q1_ly=[10.0]))
    assert not q.revenue_decline_2q["A"].iloc[0]
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[9.0], revenue_q1_ly=[10.0]))
    assert q.revenue_decline_2q["A"].iloc[0]


def test_net_debt_to_ebitda_handles_negative_ebitda():
    q = _q(_fund(2, operating_income_ttm=[-5.0, 10.0], da_ttm=[1.0, 0.0], debt=[100.0, 100.0],
                 cash=[0.0, 0.0], equity=[50.0, 50.0]))
    assert np.isinf(q.nd_ebitda["A"].iloc[0])        # debt but no earnings: fails any cap
    assert q.nd_ebitda["A"].iloc[1] == 10.0


def test_fcf_score_levels():
    n = YEAR + 1
    q = _q(_fund(n, ocf_ttm=[10.0] * YEAR + [20.0], capex_ttm=[0.0] * n))
    assert q.fcf_score["A"].iloc[-1] == 1.0
    q = _q(_fund(n, ocf_ttm=[-10.0] * YEAR + [-20.0], capex_ttm=[0.0] * n))
    assert q.fcf_score["A"].iloc[-1] == 0.0


def test_balance_score_is_linear_between_one_and_the_cap():
    s = balance_score(pd.DataFrame({"A": [0.5, 2.5, 4.0, np.inf]}), cap=4.0)
    assert s["A"].tolist() == [1.0, 0.5, 0.0, 0.0]


def test_quality_is_truncation_invariant():
    n = YEAR + 30
    rng = np.random.default_rng(2)
    f = _fund(n, revenue_ttm=rng.uniform(90, 110, n), operating_income_ttm=rng.uniform(5, 15, n),
              net_income_ttm=rng.uniform(1, 9, n), equity_avg=[50.0] * n, equity=[50.0] * n)
    d = f["revenue_ttm"].index
    assert_truncation_invariant(lambda end: _q(f if end is None else f.upto(end)),
                                [d[YEAR + 5], d[YEAR + 20]])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_quality.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.features.quality'`.

- [ ] **Step 3: Write `quality.py`**

`src/analyzer/features/quality.py`:

```python
"""Quality features (spec 5.2, without guidance tone) and their 0-1 sub-scores (spec 7.3).

"One year earlier" is the panel value `year_days` trading days earlier: the figure as it was
known then, so nothing here can look ahead."""

from __future__ import annotations

from dataclasses import dataclass

import warnings

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules, ScoreRules
from analyzer.panel.fundamentals import FundamentalPanels


def _f(f: FundamentalPanels, name: str) -> pd.DataFrame:
    return f[name].astype("float64")


def _growth(now: pd.DataFrame, before: pd.DataFrame) -> pd.DataFrame:
    return (now / before - 1).where(before > 0)


def _scaled(x: pd.DataFrame, scale: float) -> pd.DataFrame:
    """0.5 at zero, 1.0 at +scale, 0.0 at -scale."""
    return (0.5 + x / (2 * scale)).clip(0, 1)


def balance_score(nd_ebitda: pd.DataFrame, cap: float) -> pd.DataFrame:
    """1.0 at net debt / EBITDA <= 1, 0.0 at the cap or above, linear between."""
    return (1 - (nd_ebitda - 1) / (cap - 1)).clip(0, 1).where(nd_ebitda.notna())


@dataclass(frozen=True)
class QualityFeatures:
    revenue_growth: pd.DataFrame
    revenue_q_growth: pd.DataFrame
    eps_growth: pd.DataFrame
    revenue_decline_2q: pd.DataFrame
    margin_change: pd.DataFrame
    fcf: pd.DataFrame
    fcf_prev: pd.DataFrame
    nd_ebitda: pd.DataFrame
    roe_change: pd.DataFrame
    growth_score: pd.DataFrame
    margin_score: pd.DataFrame
    roe_score: pd.DataFrame
    fcf_score: pd.DataFrame


def quality_features(f: FundamentalPanels, net_debt: pd.DataFrame, features: FeatureRules,
                     score: ScoreRules) -> QualityFeatures:
    year = features.year_days
    rev = _f(f, "revenue_ttm")
    revenue_growth = _growth(rev, rev.shift(year))
    revenue_q_growth = _growth(_f(f, "revenue_q0"), _f(f, "revenue_q0_ly"))
    eps = _f(f, "eps_ttm")
    eps_prev = eps.shift(year)
    eps_growth = ((eps - eps_prev) / eps_prev.abs()).where(eps_prev != 0).clip(-1, 1)
    decline = ((_f(f, "revenue_q0") < _f(f, "revenue_q0_ly"))
               & (_f(f, "revenue_q1") < _f(f, "revenue_q1_ly")))

    margin = (_f(f, "operating_income_ttm") / rev).where(rev > 0)
    margin_change = margin - margin.shift(year)

    fcf = _f(f, "ocf_ttm") - _f(f, "capex_ttm")
    fcf_prev = fcf.shift(year)

    ebitda = _f(f, "operating_income_ttm") + _f(f, "da_ttm")
    with np.errstate(divide="ignore", invalid="ignore"):
        nd = np.where(ebitda > 0, net_debt / ebitda, np.where(net_debt > 0, np.inf, 0.0))
    nd_ebitda = pd.DataFrame(nd, index=rev.index, columns=rev.columns).where(
        ebitda.notna() & net_debt.notna())

    eq = _f(f, "equity_avg")
    roe = (_f(f, "net_income_ttm") / eq).where(eq > 0)
    roe_change = roe - roe.shift(year)

    with warnings.catch_warnings():  # an all-NaN slice gives NaN, which is what we want
        warnings.simplefilter("ignore", RuntimeWarning)
        growth_x = np.nanmean(np.stack([revenue_growth.to_numpy(), revenue_q_growth.to_numpy(),
                                        eps_growth.to_numpy()]), axis=0)
    growth_score = _scaled(pd.DataFrame(growth_x, index=rev.index, columns=rev.columns),
                           score.growth_scale)
    rising = fcf >= fcf_prev  # False when last year's value is unknown
    fcf_score = pd.DataFrame(np.where(fcf > 0, np.where(rising, 1.0, 0.5),
                                      np.where(rising, 0.25, 0.0)),
                             index=rev.index, columns=rev.columns).where(fcf.notna())
    def f32(x: pd.DataFrame) -> pd.DataFrame:  # full-universe panels: halve the memory
        return x.astype("float32")

    return QualityFeatures(
        revenue_growth=f32(revenue_growth), revenue_q_growth=f32(revenue_q_growth),
        eps_growth=f32(eps_growth), revenue_decline_2q=decline.astype(bool),
        margin_change=f32(margin_change), fcf=f32(fcf), fcf_prev=f32(fcf_prev),
        nd_ebitda=f32(nd_ebitda), roe_change=f32(roe_change), growth_score=f32(growth_score),
        margin_score=f32(_scaled(margin_change, score.margin_scale)),
        roe_score=f32(_scaled(roe_change, score.roe_scale)), fcf_score=f32(fcf_score))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_quality.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features/quality.py tests/features/test_quality.py
git commit -m "feat: quality features and sub-scores"
```

---

### Task 11: Dislocation features and variable-window helpers

**Files:**
- Create: `src/analyzer/features/windows.py`
- Create: `src/analyzer/features/dislocation.py`
- Test: `tests/features/test_windows.py`, `tests/features/test_dislocation.py`

**Interfaces:**
- Consumes: `PricePanels`, `FeatureRules`, `GateRules`.
- Produces:
  - `windows.since_max(values: np.ndarray, window: int) -> np.ndarray` (trading days since the latest maximum inside a full trailing window; NaN where the window has a gap);
  - `windows.reduce_since(values: np.ndarray, since: np.ndarray, window: int, how: str) -> np.ndarray` (`how` in `"min"`, `"sum"`, `"count"`; reduces each column over the last `since + 1` rows, NaN values ignored);
  - `windows.days_since(flag: DataFrame) -> DataFrame` (rows since the flag was last True; NaN before the first True);
  - `Dislocation` (frozen dataclass of `DataFrame`s): `high`, `since_high`, `drawdown`, `gap`, `shock` (bool), `correction` (bool), `window_low`, `dislocated` (bool, gate 2);
  - `dislocation(prices, etf_adj, spy_close, ref_of, features, gates) -> Dislocation`, with `etf_adj` = adjusted closes of the reference ETFs (columns = ETF symbols, must include `"SPY"`), `spy_close: Series`, `ref_of: Mapping[symbol, etf]`.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_windows.py`:

```python
import numpy as np
import pandas as pd

from analyzer.features.windows import days_since, reduce_since, since_max


def test_since_max_counts_back_to_the_latest_high():
    v = np.array([[1.0], [5.0], [3.0], [2.0], [5.0], [4.0]])
    s = since_max(v, window=3)
    assert np.isnan(s[:2, 0]).all()
    assert s[2:, 0].tolist() == [1.0, 2.0, 0.0, 1.0]


def test_since_max_is_nan_over_a_gap():
    v = np.array([[1.0], [np.nan], [3.0], [2.0], [1.0]])
    s = since_max(v, 3)
    assert np.isnan(s[2, 0]) and np.isnan(s[3, 0])     # windows that include the gap
    assert s[4, 0] == 2.0


def test_reduce_since_min_and_sum():
    v = np.array([[4.0], [3.0], [5.0], [1.0]])
    since = np.array([[np.nan], [1.0], [0.0], [2.0]])
    assert reduce_since(v, since, 3, "min")[:, 0].tolist()[1:] == [3.0, 5.0, 1.0]
    assert reduce_since(v, since, 3, "sum")[3, 0] == 9.0


def test_days_since():
    f = pd.DataFrame({"A": [False, True, False, False, True]})
    assert days_since(f)["A"].tolist()[1:] == [0.0, 1.0, 2.0, 0.0]
    assert np.isnan(days_since(f)["A"].iloc[0])
```

`tests/features/test_dislocation.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.dislocation import dislocation
from tests.panel_fixtures import assert_truncation_invariant, cut, make_price_panels, random_walk

RULES = load_rules()
F, G = RULES.features, RULES.gates


def _run(closes, etf=None, spy=None):
    idx = closes.index
    etf = etf if etf is not None else pd.Series(100.0, index=idx)
    spy = spy if spy is not None else pd.Series(100.0, index=idx)
    etf_adj = pd.DataFrame({"XLK": etf, "SPY": spy})
    return dislocation(make_price_panels(closes), etf_adj, spy, {c: "XLK" for c in closes}, F, G)


def test_drift_drop_is_dislocated_and_not_a_shock():
    up = list(np.linspace(80, 100, 70))
    down = list(np.linspace(100, 80, 31))[1:]
    closes = pd.DataFrame({"A": up + down}, index=pd.bdate_range("2023-01-02", periods=100))
    d = _run(closes)
    last = closes.index[-1]
    assert abs(d.drawdown.loc[last, "A"] - 0.20) < 1e-9
    assert d.since_high.loc[last, "A"] == 30
    assert d.dislocated.loc[last, "A"]
    assert not d.shock.loc[last, "A"] and not d.correction.loc[last, "A"]
    assert d.window_low.loc[last, "A"] == 80


def test_one_big_day_is_a_shock():
    vals = [100.0] * 70 + [92.0] + [90.0] * 9
    closes = pd.DataFrame({"A": vals}, index=pd.bdate_range("2023-01-02", periods=80))
    assert _run(closes).shock.iloc[-1]["A"]


def test_spy_drawdown_makes_a_correction():
    idx = pd.bdate_range("2023-01-02", periods=100)
    spy = pd.Series([100.0] * 80 + [91.0] * 20, index=idx)
    closes = pd.DataFrame({"A": [100.0] * 70 + [80.0] * 30}, index=idx)
    assert _run(closes, spy=spy).correction.iloc[-1]["A"]


def test_gap_vs_reference_etf():
    idx = pd.bdate_range("2023-01-02", periods=100)
    closes = pd.DataFrame({"A": [100.0] * 37 + [80.0] * 63}, index=idx)
    etf = pd.Series([100.0] * 37 + [95.0] * 63, index=idx)
    g = _run(closes, etf=etf).gap
    assert abs(g.iloc[-1]["A"] - (-0.20 + 0.05)) < 1e-9


def test_dislocation_is_truncation_invariant():
    closes = random_walk(["A", "B"], n=200, seed=3)
    spy = random_walk(["SPY"], n=200, seed=4)["SPY"]
    etf = pd.DataFrame({"XLK": random_walk(["X"], n=200, seed=5)["X"], "SPY": spy})

    def compute(end):
        return dislocation(cut(make_price_panels(closes), end), cut(etf, end), cut(spy, end),
                           {"A": "XLK", "B": "XLK"}, F, G)
    assert_truncation_invariant(compute, [closes.index[90], closes.index[150]])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_windows.py tests/features/test_dislocation.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `windows.py`**

`src/analyzer/features/windows.py`:

```python
"""Trailing-window helpers on 2-D arrays (rows = dates, columns = symbols). Every result at row
t uses rows up to t only. Columns are processed in chunks to bound memory."""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

_CHUNK = 64


def since_max(values: np.ndarray, window: int) -> np.ndarray:
    n, m = values.shape
    out = np.full((n, m), np.nan)
    if n < window:
        return out
    for c0 in range(0, m, _CHUNK):
        v = values[:, c0:c0 + _CHUNK]
        win = sliding_window_view(v, window, axis=0)            # (n-window+1, k, window)
        gap = np.isnan(win).any(axis=2)
        rev = np.where(np.isnan(win), -np.inf, win)[..., ::-1]   # newest first: argmax = latest max
        since = np.argmax(rev, axis=2).astype(float)
        since[gap] = np.nan
        out[window - 1:, c0:c0 + _CHUNK] = since
    return out


def reduce_since(values: np.ndarray, since: np.ndarray, window: int, how: str) -> np.ndarray:
    """Reduce each column over its last `since[t] + 1` rows (since < window)."""
    n, m = values.shape
    out = np.full((n, m), np.nan)
    if n == 0:
        return out
    pad = np.vstack([np.full((window - 1, m), np.nan), values])
    age = np.arange(window)[::-1]                               # window position -> rows back
    for c0 in range(0, m, _CHUNK):
        win = sliding_window_view(pad[:, c0:c0 + _CHUNK], window, axis=0)   # (n, k, window)
        s = since[:, c0:c0 + _CHUNK]
        keep = age[None, None, :] <= s[:, :, None]                      # NaN since -> all False
        vals = np.where(keep, win, np.nan)
        with np.errstate(all="ignore"):
            if how == "min":
                r = np.nanmin(np.where(np.isnan(vals), np.inf, vals), axis=2)
                r[np.isinf(r)] = np.nan
            elif how == "sum":
                r = np.nansum(vals, axis=2)
            elif how == "count":
                r = (~np.isnan(vals)).sum(axis=2).astype(float)
            else:
                raise ValueError(how)
        r[np.isnan(s)] = np.nan
        out[:, c0:c0 + _CHUNK] = r
    return out


def days_since(flag: pd.DataFrame) -> pd.DataFrame:
    pos = pd.DataFrame(np.arange(len(flag), dtype=float)[:, None].repeat(flag.shape[1], axis=1),
                       index=flag.index, columns=flag.columns)
    last = pos.where(flag.fillna(False).astype(bool)).ffill()
    return pos - last
```

- [ ] **Step 4: Write `dislocation.py`**

`src/analyzer/features/dislocation.py`:

```python
"""Dislocation (spec 5.3) and gate 2 (spec 7.1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules, GateRules
from analyzer.features.windows import days_since, reduce_since, since_max
from analyzer.panel.prices import PricePanels


@dataclass(frozen=True)
class Dislocation:
    high: pd.DataFrame
    since_high: pd.DataFrame
    drawdown: pd.DataFrame
    gap: pd.DataFrame
    shock: pd.DataFrame
    correction: pd.DataFrame
    window_low: pd.DataFrame
    dislocated: pd.DataFrame


def reference_returns(etf_adj: pd.DataFrame, ref_of: Mapping[str, str], columns, days: int
                      ) -> pd.DataFrame:
    """Each stock's reference-ETF return over `days`, as a stock-shaped frame."""
    ret = etf_adj / etf_adj.shift(days) - 1
    refs = [ref_of.get(c, "SPY") for c in columns]
    out = ret.reindex(columns=refs)
    out.columns = list(columns)
    return out


def dislocation(prices: PricePanels, etf_adj: pd.DataFrame, spy_close: pd.Series,
                ref_of: Mapping[str, str], features: FeatureRules, gates: GateRules) -> Dislocation:
    w = features.high_window
    close = prices.close
    high = close.rolling(w, min_periods=w).max()
    since = pd.DataFrame(since_max(close.to_numpy(dtype="float64"), w),
                         index=close.index, columns=close.columns)
    drawdown = 1 - close / high
    stock_ret = prices.adj_close / prices.adj_close.shift(w) - 1
    gap = stock_ret - reference_returns(etf_adj, ref_of, close.columns, w)

    daily = close / close.shift(1) - 1
    shock_since = days_since(daily <= -features.shock_day_drop)
    shock = shock_since < since          # a shock day after the high; NaN compares False

    spy_dd = 1 - spy_close / spy_close.rolling(w, min_periods=w).max()
    spy_since = days_since((spy_dd >= features.correction_drawdown).to_frame("SPY"))["SPY"]
    spy_frame = pd.DataFrame(np.repeat(spy_since.to_numpy()[:, None], close.shape[1], axis=1),
                             index=close.index, columns=close.columns)
    correction = spy_frame <= since      # SPY was in correction at some point since the high

    low = prices.low.to_numpy(dtype="float64")
    window_low = pd.DataFrame(reduce_since(low, since.to_numpy(), w, "min"),
                              index=close.index, columns=close.columns)
    dislocated = (drawdown >= gates.min_drawdown) & (gap <= -gates.min_gap_vs_etf)
    return Dislocation(high=high, since_high=since, drawdown=drawdown, gap=gap,
                       shock=shock.astype(bool), correction=correction.astype(bool),
                       window_low=window_low, dislocated=dislocated.astype(bool))
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/features/test_windows.py tests/features/test_dislocation.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/features/windows.py src/analyzer/features/dislocation.py \
        tests/features/test_windows.py tests/features/test_dislocation.py
git commit -m "feat: dislocation features, gate 2 and trailing-window helpers"
```

---
### Task 12: Technical features

**Files:**
- Create: `src/analyzer/features/technical.py`
- Test: `tests/features/test_technical.py`

**Interfaces:**
- Consumes: `PricePanels`, `FeatureRules`.
- Produces:
  - `rsi(close, n) -> DataFrame`; `atr(high, low, close, n) -> DataFrame`;
  - `confirmed_lows(low, k) -> DataFrame[bool]` (True at t when the bar at t − k was a swing low);
  - `PivotPairs` (frozen dataclass of `DataFrame`s `last_price, prev_price, last_osc, prev_osc, prev_pos`) and `pivot_pairs(price, osc, confirmed, k) -> PivotPairs`;
  - `bullish_divergence(pairs, lookback) -> DataFrame[bool]`;
  - `reclaimed_support(close, pairs, bars) -> DataFrame[bool]`;
  - `weekly_usable_dates(index) -> Series` (week period → first trading day of the following week; NaT for the last week);
  - `weekly_divergence(close, low, rules) -> DataFrame[bool]` (daily index);
  - `Technical` (frozen dataclass: `rsi, atr, ma, divergence, reclaimed`) and `technical(prices, rules) -> Technical`. The pivot pairs are not kept, to bound memory.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_technical.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.technical import (
    atr,
    bullish_divergence,
    confirmed_lows,
    pivot_pairs,
    reclaimed_support,
    rsi,
    weekly_divergence,
    weekly_usable_dates,
)
from tests.panel_fixtures import assert_truncation_invariant, cut, random_walk

F = load_rules().features


def test_rsi_bounds_and_direction():
    up = pd.DataFrame({"A": np.arange(1.0, 40.0)})
    assert rsi(up, 14)["A"].iloc[-1] == 100.0
    down = pd.DataFrame({"A": np.arange(40.0, 1.0, -1)})
    assert rsi(down, 14)["A"].iloc[-1] < 1.0


def test_atr_of_constant_range():
    idx = pd.RangeIndex(30)
    h = pd.DataFrame({"A": [11.0] * 30}, index=idx)
    lo = pd.DataFrame({"A": [9.0] * 30}, index=idx)
    c = pd.DataFrame({"A": [10.0] * 30}, index=idx)
    assert abs(atr(h, lo, c, 14)["A"].iloc[-1] - 2.0) < 1e-9


def test_pivot_is_known_only_k_bars_later():
    low = pd.DataFrame({"A": [5.0, 4, 3, 2, 3, 4, 5, 6]})
    conf = confirmed_lows(low, 2)
    assert conf["A"].tolist() == [False, False, False, False, False, True, False, False]


def _divergence_series():
    # Two swing lows: price 10 then 8 (lower low); the second fall is gentler, so RSI is higher.
    price = [20, 18, 15, 12, 10, 13, 16, 17, 16, 15, 14, 13, 12, 11, 10, 9.5, 9, 8.5, 8, 9, 10, 11]
    return pd.DataFrame({"A": np.array(price, dtype=float)})


def test_bullish_divergence_after_lower_low_with_higher_rsi():
    low = _divergence_series()
    osc = pd.DataFrame({"A": [50, 40, 30, 25, 20, 35, 50, 55, 50, 45, 42, 40, 38, 36, 34, 33, 32,
                              31, 30, 40, 50, 55]}, dtype=float)
    k = 2
    pairs = pivot_pairs(low, osc, confirmed_lows(low, k), k)
    div = bullish_divergence(pairs, lookback=40)
    assert div["A"].iloc[-1]                     # pivots at 4 (10, osc 20) and 18 (8, osc 30)
    assert not div["A"].iloc[19]                 # second pivot not confirmed until row 20


def test_reclaimed_support():
    low = _divergence_series()
    k = 2
    pairs = pivot_pairs(low, low, confirmed_lows(low, k), k)
    close = low.copy()
    rec = reclaimed_support(close, pairs, bars=10)
    assert rec["A"].iloc[-1]                     # back above the old swing low of 10 after 8


def test_weekly_usable_dates_start_next_week():
    idx = pd.bdate_range("2024-01-01", "2024-01-17")   # Mon 1st .. Wed 17th
    u = weekly_usable_dates(idx)
    assert u.iloc[0] == pd.Timestamp("2024-01-08")
    assert u.iloc[1] == pd.Timestamp("2024-01-15")
    assert pd.isna(u.iloc[-1])


def test_weekly_bar_waits_for_next_week():
    closes = random_walk(["A", "B"], n=600, seed=11)
    wednesdays = [d for d in closes.index[300:] if d.dayofweek == 2][:3]
    assert_truncation_invariant(
        lambda end: weekly_divergence(cut(closes, end), cut(closes, end), F), wednesdays)


def test_daily_features_are_truncation_invariant():
    from analyzer.features.technical import technical
    from tests.panel_fixtures import make_price_panels
    p = make_price_panels(random_walk(["A", "B"], n=300, seed=12))
    t = lambda end: (lambda r: {"rsi": r.rsi, "atr": r.atr, "div": r.divergence,
                                "rec": r.reclaimed})(technical(cut(p, end), F))
    assert_truncation_invariant(t, [p.dates[120], p.dates[250]])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_technical.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `technical.py`**

`src/analyzer/features/technical.py`:

```python
"""Technical features (spec 5.5). A swing pivot needs `k` bars after it, so it is known only
`k` bars later; a weekly bar is known from the first trading day of the next week."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.panel.prices import PricePanels


def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss > 0, 100.0).where(gain.notna())


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int) -> pd.DataFrame:
    prev = close.shift(1)
    tr = np.fmax(np.fmax(high - low, (high - prev).abs()), (low - prev).abs())
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def confirmed_lows(low: pd.DataFrame, k: int) -> pd.DataFrame:
    span = 2 * k + 1
    is_low = low.eq(low.rolling(span, center=True, min_periods=span).min())
    return is_low.shift(k, fill_value=False).astype(bool)


@dataclass(frozen=True)
class PivotPairs:
    last_price: pd.DataFrame
    prev_price: pd.DataFrame
    last_osc: pd.DataFrame
    prev_osc: pd.DataFrame
    prev_pos: pd.DataFrame


def pivot_pairs(price: pd.DataFrame, osc: pd.DataFrame, confirmed: pd.DataFrame, k: int) -> PivotPairs:
    """For each row: the latest and the previous confirmed swing low (price and oscillator at the
    pivot bar) and the row position of the previous one."""
    n, m = price.shape
    pos = np.arange(n, dtype=float) - k
    at_price = price.shift(k).to_numpy(dtype="float64")
    at_osc = osc.shift(k).to_numpy(dtype="float64")
    conf = confirmed.to_numpy(dtype=bool)
    names = ("last_price", "prev_price", "last_osc", "prev_osc", "prev_pos")
    out = {name: np.full((n, m), np.nan) for name in names}
    rows_all = np.arange(n)
    for j in range(m):
        rows = np.flatnonzero(conf[:, j])
        if len(rows) == 0:
            continue
        last = np.searchsorted(rows, rows_all, side="right") - 1
        has, has_prev = last >= 0, last >= 1
        out["last_price"][has, j] = at_price[rows[last[has]], j]
        out["last_osc"][has, j] = at_osc[rows[last[has]], j]
        prev_rows = rows[last[has_prev] - 1]
        out["prev_price"][has_prev, j] = at_price[prev_rows, j]
        out["prev_osc"][has_prev, j] = at_osc[prev_rows, j]
        out["prev_pos"][has_prev, j] = pos[prev_rows]
    return PivotPairs(**{name: pd.DataFrame(v, index=price.index, columns=price.columns)
                         for name, v in out.items()})


def bullish_divergence(pairs: PivotPairs, lookback: int) -> pd.DataFrame:
    """Price made a lower low while the oscillator made a higher low, both pivots recent."""
    t = np.arange(len(pairs.last_price), dtype=float)[:, None]
    recent = pd.DataFrame((t - pairs.prev_pos.to_numpy()) <= lookback,
                          index=pairs.last_price.index, columns=pairs.last_price.columns)
    return (recent & (pairs.last_price < pairs.prev_price)
            & (pairs.last_osc > pairs.prev_osc)).astype(bool)


def reclaimed_support(close: pd.DataFrame, pairs: PivotPairs, bars: int) -> pd.DataFrame:
    """A lower swing low broke the previous one, and the close is back above that old level
    after being at or below it within the last `bars` days."""
    level = pairs.prev_price
    broken = pairs.last_price < level
    below = (close <= level).astype(float)
    recently_below = below.shift(1).rolling(bars, min_periods=1).max() > 0
    return (broken & (close > level) & recently_below).astype(bool)


def weekly_usable_dates(index: pd.DatetimeIndex) -> pd.Series:
    week = index.to_period("W-FRI")
    first_day = pd.Series(index, index=week).groupby(level=0).first()
    return first_day.shift(-1)


def weekly_divergence(close: pd.DataFrame, low: pd.DataFrame, rules: FeatureRules) -> pd.DataFrame:
    week = close.index.to_period("W-FRI")
    wclose = close.groupby(week).last()
    wlow = low.groupby(week).min()
    k = rules.weekly_pivot_bars
    pairs = pivot_pairs(wlow, rsi(wclose, rules.rsi_period), confirmed_lows(wlow, k), k)
    div = bullish_divergence(pairs, rules.weekly_divergence_lookback).astype(float)
    usable = weekly_usable_dates(close.index).reindex(div.index)
    div = div[usable.notna().to_numpy()]
    div.index = pd.DatetimeIndex(usable.dropna().to_numpy())
    return div.reindex(close.index).ffill().fillna(0.0).astype(bool)


@dataclass(frozen=True)
class Technical:
    rsi: pd.DataFrame
    atr: pd.DataFrame
    ma: pd.DataFrame
    divergence: pd.DataFrame
    reclaimed: pd.DataFrame


def technical(prices: PricePanels, rules: FeatureRules) -> Technical:
    close, low = prices.close, prices.low
    r = rsi(close, rules.rsi_period)
    k = rules.pivot_bars
    pairs = pivot_pairs(low, r, confirmed_lows(low, k), k)
    daily = bullish_divergence(pairs, rules.divergence_lookback)
    weekly = weekly_divergence(close, low, rules)
    return Technical(
        rsi=r,
        atr=atr(prices.high, low, close, rules.atr_period),
        ma=close.rolling(rules.ma_period, min_periods=rules.ma_period).mean(),
        divergence=daily | weekly,
        reclaimed=reclaimed_support(close, pairs, rules.reclaim_bars),
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_technical.py -v`
Expected: all PASS. The divergence fixture has pivots (k = 2) at rows 4 (price 10) and 18 (price 8); the second is confirmed at row 20.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features/technical.py tests/features/test_technical.py
git commit -m "feat: RSI, ATR, confirmed pivots, daily and weekly divergence, reclaimed support"
```

---

### Task 13: Flow features

**Files:**
- Create: `src/analyzer/features/flow.py`
- Test: `tests/features/test_flow.py`

**Interfaces:**
- Consumes: `PricePanels`, `FeatureRules`, `reduce_since` (Task 11), `confirmed_lows`, `pivot_pairs`, `bullish_divergence` (Task 12).
- Produces:
  - `HOT, NEUTRAL, COLD = 1.0, 0.0, -1.0`;
  - `active_etfs(eligible, ref_of, etfs) -> DataFrame[bool]` (an ETF counts on a date when at least one eligible stock uses it);
  - `etf_flow_codes(etf, spy_adj, active, rules) -> DataFrame` (date × ETF: 1 hot, 0 neutral, -1 cold, NaN unknown);
  - `stock_codes(codes, ref_of, columns) -> DataFrame` (date × stock);
  - `Flow` (frozen dataclass: `industry, updown_ratio, obv_divergence, accumulation`) and `flow(prices, etf, spy_adj, eligible, ref_of, since_high, rules) -> Flow`.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_flow.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.flow import COLD, HOT, NEUTRAL, active_etfs, etf_flow_codes, flow
from analyzer.panel.prices import PricePanels
from tests.panel_fixtures import assert_truncation_invariant, cut, make_price_panels, random_walk

F = load_rules().features


def _etfs(n=200):
    idx = pd.bdate_range("2022-01-03", periods=n)
    grow = lambda r: 100 * (1 + r) ** np.arange(n)
    closes = pd.DataFrame({f"E{i}": grow(r) for i, r in enumerate([0.004, 0.002, 0.0, -0.002, -0.004])},
                          index=idx)
    return closes


def test_flow_codes_rank_industries():
    closes = _etfs()
    p = make_price_panels(closes, volume=1e6)
    vol = p.volume.copy()
    vol.iloc[-20:, 0] = 3e6                               # E0: rising dollar volume
    p = PricePanels(p.open, p.high, p.low, p.close, p.adj_close, vol)
    spy = pd.Series(100.0, index=closes.index)
    active = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    codes = etf_flow_codes(p, spy, active, F)
    last = codes.iloc[-1]
    assert last["E0"] == HOT and last["E4"] == COLD and last["E2"] == NEUTRAL
    assert last["E1"] == NEUTRAL                          # top 40%, volume flat: not hot


def test_inactive_etfs_are_not_ranked():
    closes = _etfs()
    active = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    active["E0"] = False
    codes = etf_flow_codes(make_price_panels(closes), pd.Series(100.0, index=closes.index), active, F)
    assert np.isnan(codes.iloc[-1]["E0"])


def test_active_etfs():
    idx = pd.bdate_range("2024-01-01", periods=2)
    eligible = pd.DataFrame({"A": [True, False], "B": [False, False]}, index=idx)
    a = active_etfs(eligible, {"A": "XLK", "B": "XLE"}, ["XLK", "XLE"])
    assert a["XLK"].tolist() == [True, False] and not a["XLE"].any()


def test_flow_is_truncation_invariant():
    stocks = make_price_panels(random_walk(["A", "B"], n=260, seed=21))
    etf = make_price_panels(random_walk(["XLK", "XLE"], n=260, seed=22))
    spy = random_walk(["SPY"], n=260, seed=23)["SPY"]
    eligible = pd.DataFrame(True, index=stocks.dates, columns=["A", "B"])
    since = pd.DataFrame(10.0, index=stocks.dates, columns=["A", "B"])
    refs = {"A": "XLK", "B": "XLE"}

    def compute(end):
        r = flow(cut(stocks, end), cut(etf, end), cut(spy, end), cut(eligible, end), refs,
                 cut(since, end), F)
        return {"industry": r.industry, "acc": r.accumulation}
    assert_truncation_invariant(compute, [stocks.dates[150], stocks.dates[230]])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_flow.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `flow.py`**

`src/analyzer/features/flow.py`:

```python
"""Flow (spec 5.4): money going into the stock's industry, and accumulation during the dip."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.features.technical import bullish_divergence, confirmed_lows, pivot_pairs
from analyzer.features.windows import reduce_since
from analyzer.panel.prices import PricePanels

HOT, NEUTRAL, COLD = 1.0, 0.0, -1.0


def active_etfs(eligible: pd.DataFrame, ref_of: Mapping[str, str], etfs: Sequence[str]) -> pd.DataFrame:
    out = pd.DataFrame(False, index=eligible.index, columns=list(etfs))
    for etf in etfs:
        cols = [c for c in eligible.columns if ref_of.get(c) == etf]
        if cols:
            out[etf] = eligible[cols].any(axis=1)
    return out


def etf_flow_codes(etf: PricePanels, spy_adj: pd.Series, active: pd.DataFrame,
                   rules: FeatureRules) -> pd.DataFrame:
    adj = etf.adj_close

    def excess(days: int) -> pd.DataFrame:
        return (adj / adj.shift(days) - 1).sub(spy_adj / spy_adj.shift(days) - 1, axis=0)

    rs = (excess(rules.rs_short) + excess(rules.rs_long)) / 2
    dv = etf.close * etf.volume
    trend = (dv.rolling(rules.dv_short, min_periods=rules.dv_short).mean()
             / dv.rolling(rules.dv_long, min_periods=rules.dv_long).mean())
    mask = active.reindex(index=rs.index, columns=rs.columns).fillna(False).astype(bool)
    rank = rs.where(mask).rank(axis=1, pct=True)
    hot = (rank >= rules.hot_rank) & (trend > rules.hot_dv_trend)
    cold = rank <= rules.cold_rank
    codes = pd.DataFrame(np.where(hot, HOT, np.where(cold, COLD, NEUTRAL)),
                         index=rs.index, columns=rs.columns)
    return codes.where(rank.notna())


def stock_codes(codes: pd.DataFrame, ref_of: Mapping[str, str], columns: Sequence[str]) -> pd.DataFrame:
    out = codes.reindex(columns=[ref_of.get(c, "SPY") for c in columns])
    out.columns = list(columns)
    return out


@dataclass(frozen=True)
class Flow:
    industry: pd.DataFrame
    updown_ratio: pd.DataFrame
    obv_divergence: pd.DataFrame
    accumulation: pd.DataFrame


def flow(prices: PricePanels, etf: PricePanels, spy_adj: pd.Series, eligible: pd.DataFrame,
         ref_of: Mapping[str, str], since_high: pd.DataFrame, rules: FeatureRules) -> Flow:
    close, volume = prices.close, prices.volume
    active = active_etfs(eligible, ref_of, list(etf.close.columns))
    industry = stock_codes(etf_flow_codes(etf, spy_adj, active, rules), ref_of, close.columns)

    change = close.diff()
    vol = volume.to_numpy(dtype="float64")
    since = since_high.reindex_like(close).to_numpy(dtype="float64")
    w = rules.high_window
    up = np.where(change > 0, vol, np.nan)
    down = np.where(change < 0, vol, np.nan)
    with np.errstate(all="ignore"):
        up_avg = reduce_since(up, since, w, "sum") / reduce_since(up, since, w, "count")
        down_avg = reduce_since(down, since, w, "sum") / reduce_since(down, since, w, "count")
        ratio = pd.DataFrame(up_avg / down_avg, index=close.index, columns=close.columns)

    obv = (np.sign(change).fillna(0) * volume.fillna(0)).cumsum()
    k = rules.pivot_bars
    pairs = pivot_pairs(prices.low, obv, confirmed_lows(prices.low, k), k)
    obv_div = bullish_divergence(pairs, rules.divergence_lookback)

    span = rules.accumulation_high - rules.accumulation_low
    ratio_score = ((ratio - rules.accumulation_low) / span).clip(0, 1).fillna(0.0)
    accumulation = 0.5 * ratio_score + 0.5 * obv_div.astype(float)
    return Flow(industry=industry, updown_ratio=ratio, obv_divergence=obv_div,
                accumulation=accumulation)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_flow.py -v`
Expected: all PASS. With five ETFs, `rank(pct=True)` gives 0.2, 0.4, 0.6, 0.8, 1.0: E0 (1.0) is hot if its volume trend is > 1.2, E4 (0.2) is cold.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features/flow.py tests/features/test_flow.py
git commit -m "feat: industry flow tags and accumulation during the drawdown"
```

---

### Task 14: Insider cluster buying

**Files:**
- Create: `src/analyzer/features/insider.py`
- Test: `tests/features/test_insider.py`

**Interfaces:**
- Consumes: table `insider_transactions`; `FeatureRules`.
- Produces:
  - `load_purchases(conn) -> DataFrame[cik:int, filing_date:datetime64, owner:str, executive:bool, usd:float]`;
  - `cluster_buying(rows, purchases, rules) -> DataFrame[buyers:int, executive_buy:bool, cluster:bool]` indexed like `rows`; `rows` has columns `cik` (float or int), `date`, `window_start` (both `datetime64`).

- [ ] **Step 1: Write the failing tests**

`tests/features/test_insider.py`:

```python
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.insider import cluster_buying, load_purchases
from analyzer.store.db import connect, migrate

F = load_rules().features


def _purchases(rows):
    df = pd.DataFrame(rows, columns=["cik", "filing_date", "owner", "executive", "usd"])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


ROWS = pd.DataFrame({"cik": [1.0, 1.0, 2.0],
                     "date": pd.to_datetime(["2024-03-01", "2024-01-10", "2024-03-01"]),
                     "window_start": pd.to_datetime(["2024-01-15", "2023-12-01", "2024-01-15"])})


def test_two_distinct_buyers_in_the_window_make_a_cluster():
    p = _purchases([(1, "2024-02-01", "Ann", False, 10_000), (1, "2024-02-05", "Bob", False, 10_000),
                    (1, "2024-03-05", "Cy", False, 10_000)])          # filed after the date
    out = cluster_buying(ROWS, p, F)
    assert out.loc[0, "buyers"] == 2 and out.loc[0, "cluster"]
    assert not out.loc[1, "cluster"] and out.loc[1, "buyers"] == 0
    assert not out.loc[2, "cluster"]


def test_one_large_executive_purchase_is_enough():
    p = _purchases([(2, "2024-02-01", "Dee", True, 150_000)])
    out = cluster_buying(ROWS, p, F)
    assert out.loc[2, "executive_buy"] and out.loc[2, "cluster"]


def test_load_purchases_marks_executives(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO insider_transactions (accession, seq, issuer_cik, filing_date, owner_names, "
                 "relationship, officer_title, trans_date, trans_code, shares, price, acquired_disposed, "
                 "source) VALUES ('a', 1, 7, '2024-02-01', 'Doe Jane', 'Officer', 'Chief Executive Officer', "
                 "'2024-01-30', 'P', 1000, 120.0, 'A', 'bulk')")
    conn.execute("INSERT INTO insider_transactions (accession, seq, issuer_cik, filing_date, owner_names, "
                 "trans_code, shares, price, source) VALUES ('b', 1, 7, '2024-02-01', 'X', 'S', 1, 1, 'bulk')")
    p = load_purchases(conn)
    assert len(p) == 1 and p.iloc[0]["executive"] and p.iloc[0]["usd"] == 120_000
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_insider.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `insider.py`**

`src/analyzer/features/insider.py`:

```python
"""Insider cluster buying during the drawdown window (spec 5.2). A purchase is known from its
Form 4 filing date, never its transaction date."""

from __future__ import annotations

import re

import pandas as pd

from analyzer.config import FeatureRules

EXECUTIVE = re.compile(
    r"\b(CEO|CFO|chief executive|chief financial|principal executive|principal financial)\b", re.I)


def load_purchases(conn) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT issuer_cik AS cik, filing_date, owner_names AS owner, officer_title, shares, price "
        "FROM insider_transactions WHERE trans_code = 'P' "
        "AND COALESCE(acquired_disposed, 'A') = 'A'", conn)
    df["filing_date"] = pd.to_datetime(df["filing_date"]).astype("datetime64[ns]")
    df["executive"] = df["officer_title"].fillna("").str.contains(EXECUTIVE)
    df["usd"] = df["shares"].fillna(0) * df["price"].fillna(0)
    return df[["cik", "filing_date", "owner", "executive", "usd"]]


def cluster_buying(rows: pd.DataFrame, purchases: pd.DataFrame, rules: FeatureRules) -> pd.DataFrame:
    out = pd.DataFrame({"buyers": 0, "executive_buy": False, "cluster": False}, index=rows.index)
    if rows.empty or purchases.empty:
        return out
    r = rows[["cik", "date", "window_start"]].dropna(subset=["cik"]).copy()
    r["cik"] = r["cik"].astype("int64")
    r["row"] = r.index
    p = purchases.assign(cik=purchases["cik"].astype("int64"))
    m = r.merge(p, on="cik")
    m = m[(m["filing_date"] >= m["window_start"]) & (m["filing_date"] <= m["date"])]
    if m.empty:
        return out
    buyers = m.groupby("row")["owner"].nunique()
    big_exec = (m["executive"] & (m["usd"] >= rules.insider_exec_min_usd)).groupby(m["row"]).any()
    out.loc[buyers.index, "buyers"] = buyers.astype(int)
    out.loc[big_exec.index, "executive_buy"] = big_exec.astype(bool)
    out["cluster"] = (out["buyers"] >= rules.insider_min_buyers) | out["executive_buy"]
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_insider.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features/insider.py tests/features/test_insider.py
git commit -m "feat: insider cluster buying from Form 4 purchases"
```

---

### Task 15: Liquidity regime

**Files:**
- Create: `src/analyzer/features/regime.py`
- Test: `tests/features/test_regime.py`

**Interfaces:**
- Consumes: table `macro`.
- Produces: `PUBLICATION_LAG_DAYS: dict[str, int]`; `load_macro(conn, dates) -> DataFrame` (trading date × series, each value shifted by its publication lag and forward-filled); `regime(macro) -> Series[str]` with values `"loose" | "neutral" | "tight" | "unknown"`.

- [ ] **Step 1: Write the failing tests**

`tests/features/test_regime.py`:

```python
import numpy as np
import pandas as pd

from analyzer.features.regime import PUBLICATION_LAG_DAYS, load_macro, regime
from analyzer.store.db import connect, migrate


def test_value_is_usable_only_after_its_publication_lag(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO macro VALUES ('M2SL', '2024-01-01', 100.0)")
    dates = pd.bdate_range("2024-01-01", "2024-03-29")
    m = load_macro(conn, dates)
    first = m["M2SL"].first_valid_index()
    assert first >= pd.Timestamp("2024-01-01") + pd.Timedelta(days=PUBLICATION_LAG_DAYS["M2SL"])


def _macro(n, **cols):
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


def test_regime_counts_loose_indicators():
    n = 900
    rising = np.linspace(100, 200, n)
    falling = np.linspace(200, 100, n)
    loose = _macro(n, NFCI=-0.5, BAMLH0A0HYM2=falling, WALCL=rising, M2SL=rising,
                   RRPONTSYD=falling, DFII10=falling)
    assert regime(loose).iloc[-1] == "loose"
    tight = _macro(n, NFCI=0.5, BAMLH0A0HYM2=rising, WALCL=falling, M2SL=falling,
                   RRPONTSYD=rising, DFII10=rising)
    assert regime(tight).iloc[-1] == "tight"
    assert regime(loose).iloc[0] == "unknown"           # no 3-year median yet
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/features/test_regime.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `regime.py`**

`src/analyzer/features/regime.py`:

```python
"""Liquidity regime (spec 5.6): context for research breakdowns only, never a gate or a score.

FRED dates a value by its observation period, not by when it was published. Each series is
shifted by a conservative publication lag so a value is never used before it existed (first
release values from ALFRED are deferred; addendum 7.3)."""

from __future__ import annotations

import pandas as pd

PUBLICATION_LAG_DAYS = {
    "WALCL": 7,          # weekly, Wednesday level, published Thursday
    "RRPONTSYD": 1,      # daily
    "M2SL": 58,          # monthly, dated the 1st, published about four weeks after month end
    "NFCI": 7,           # weekly, dated Friday, published the next Wednesday
    "BAMLH0A0HYM2": 1,   # daily
    "DFII10": 1,         # daily
}
YEARS3 = 756   # trading days
WEEKS13 = 65


def load_macro(conn, dates: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT series_id, date, value FROM macro", conn)
    out = pd.DataFrame(index=dates)
    for series, lag in PUBLICATION_LAG_DAYS.items():
        s = df[df["series_id"] == series]
        if s.empty:
            out[series] = float("nan")
            continue
        known = pd.Series(s["value"].to_numpy(),
                          index=pd.to_datetime(s["date"]) + pd.Timedelta(days=lag)).sort_index()
        known = known[~known.index.duplicated(keep="last")]
        out[series] = known.reindex(known.index.union(dates)).ffill().reindex(dates)
    return out


def regime(macro: pd.DataFrame) -> pd.Series:
    hy, real = macro["BAMLH0A0HYM2"], macro["DFII10"]
    indicators = pd.DataFrame({
        "nfci": macro["NFCI"] < 0,
        "hy": hy < hy.rolling(YEARS3, min_periods=YEARS3).median(),
        "fed": macro["WALCL"] - macro["WALCL"].shift(WEEKS13) > 0,
        "m2": macro["M2SL"] / macro["M2SL"].shift(252) - 1 > 0,
        "rrp": macro["RRPONTSYD"] < macro["RRPONTSYD"].shift(WEEKS13),
        "real": real < real.rolling(YEARS3, min_periods=YEARS3).median(),
    })
    known = pd.DataFrame({
        "nfci": macro["NFCI"].notna(),
        "hy": hy.rolling(YEARS3, min_periods=YEARS3).median().notna() & hy.notna(),
        "fed": macro["WALCL"].shift(WEEKS13).notna(),
        "m2": macro["M2SL"].shift(252).notna(),
        "rrp": macro["RRPONTSYD"].shift(WEEKS13).notna(),
        "real": real.rolling(YEARS3, min_periods=YEARS3).median().notna() & real.notna(),
    })
    loose = indicators.sum(axis=1)
    out = pd.Series("neutral", index=macro.index)
    out[loose >= 4] = "loose"
    out[loose <= 2] = "tight"
    out[~known.all(axis=1)] = "unknown"
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/features/test_regime.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/features/regime.py tests/features/test_regime.py
git commit -m "feat: liquidity regime with publication lags"
```

---
### Task 16: Variants

**Files:**
- Create: `src/analyzer/signals/__init__.py` (empty)
- Create: `src/analyzer/signals/variants.py`
- Create: `tests/signals/__init__.py` (empty)
- Test: `tests/signals/test_variants.py`

**Interfaces:**
- Consumes: `VariantRules`; `entities` (Task 6); `FundamentalPanels.has_property`, `.has_us_gaap` (Task 8); `VARIANT_METRICS` (Task 9).
- Produces: constants `STANDARD = "standard"`, `FINANCIALS = "financials"`, `REIT = "reit"`, `SCORED = (STANDARD, FINANCIALS, REIT)`; `variant_of(cik_known: bool, sic, has_property, has_us_gaap, rules) -> str` (a scored variant or `"excluded:<reason>"` with reason in `no_cik`, `no_us_gaap`, `fund`, `mortgage_reit`); `variant_series(entities, f, rules) -> Series[str]`; `columns_for_metrics(variants) -> dict[str, list[str]]`.

- [ ] **Step 1: Write the failing tests**

`tests/signals/test_variants.py`:

```python
import pandas as pd

from analyzer.config import load_rules
from analyzer.signals.variants import columns_for_metrics, variant_of

V = load_rules().variants


def test_variant_rules():
    assert variant_of(True, "3674", False, True, V) == "standard"
    assert variant_of(True, "6022", False, True, V) == "financials"
    assert variant_of(True, "6411", False, True, V) == "financials"
    assert variant_of(True, "6798", True, True, V) == "reit"
    assert variant_of(True, "6798", False, True, V) == "excluded:mortgage_reit"
    assert variant_of(True, "6726", False, True, V) == "excluded:fund"
    assert variant_of(True, "3674", False, False, V) == "excluded:no_us_gaap"
    assert variant_of(False, None, False, False, V) == "excluded:no_cik"


def test_columns_for_metrics():
    v = pd.Series({"A": "standard", "B": "financials", "C": "reit", "D": "excluded:fund"})
    cols = columns_for_metrics(v)
    assert cols["pe"] == ["A", "B"]
    assert cols["ev_ebitda"] == ["A", "C"]
    assert cols["pb"] == ["B"] and cols["p_ffo"] == ["C"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/signals/test_variants.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `variants.py`**

`src/analyzer/signals/variants.py`:

```python
"""Which scoring variant applies to each symbol (spec 7.4, addendum 5.3)."""

from __future__ import annotations

import pandas as pd

from analyzer.config import VariantRules
from analyzer.features.value import METRICS, VARIANT_METRICS
from analyzer.panel.fundamentals import FundamentalPanels

STANDARD, FINANCIALS, REIT = "standard", "financials", "reit"
SCORED = (STANDARD, FINANCIALS, REIT)


def variant_of(cik_known: bool, sic: str | None, has_property: bool, has_us_gaap: bool,
               rules: VariantRules) -> str:
    if not cik_known:
        return "excluded:no_cik"
    if not has_us_gaap:
        return "excluded:no_us_gaap"     # IFRS filers, or no XBRL financials at all
    if sic in rules.excluded_sics:
        return "excluded:fund"
    if sic == rules.reit.sic:
        return REIT if has_property else "excluded:mortgage_reit"
    if sic and sic.isdigit() and rules.financials.sic_min <= int(sic) <= rules.financials.sic_max:
        return FINANCIALS
    return STANDARD


def variant_series(entities: pd.DataFrame, f: FundamentalPanels, rules: VariantRules) -> pd.Series:
    return pd.Series({
        s: variant_of(pd.notna(entities.at[s, "cik"]), entities.at[s, "sic"],
                      bool(f.has_property.get(s, False)), bool(f.has_us_gaap.get(s, False)), rules)
        for s in entities.index}, dtype=object)


def columns_for_metrics(variants: pd.Series) -> dict[str, list[str]]:
    return {m: [s for s, v in variants.items() if m in VARIANT_METRICS.get(v, ())] for m in METRICS}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/signals/test_variants.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/signals tests/signals
git commit -m "feat: scoring variants per symbol"
```

---

### Task 17: Eligibility and gates 1, 3 and 4

**Files:**
- Create: `src/analyzer/signals/gates.py`
- Test: `tests/signals/test_gates.py`

**Interfaces:**
- Consumes: `Rules`; `ValueFeatures` (Task 9); `QualityFeatures` (Task 10); `FundamentalPanels`; `SCORED`, `STANDARD`, `FINANCIALS`, `REIT` (Task 16).
- Produces:
  - `pick(frame, rows) -> np.ndarray` (values of a wide frame at the rows' `date` and `symbol`; NaN where the frame lacks the column);
  - `Eligibility` (frozen dataclass: `eligible: DataFrame[bool]`, `skipped: dict[str, int]`) and `eligibility(member, variants, f, market_cap, rules) -> Eligibility`; skip reasons, counted in this order over member ticker-days: `variant` (excluded variant), `history` (first filing less than `history_min_days` before the date), `required` (revenue, and operating income — net income for financials — missing), `market_cap`;
  - `TRAP_RULES = ("revenue", "margin", "roe", "fcf", "leverage")`;
  - `TrapResult` (frozen dataclass: `fails: DataFrame[bool]`, `missing: DataFrame[int]`, `unknown: dict[str, DataFrame[bool]]`) and `value_trap(q, variants, rules) -> TrapResult`;
  - `undervalued(rows, vf, rules) -> DataFrame` with columns `n_metrics, median_pct, cheaper, gate1` and `pct_<metric>` for every metric in `METRICS`, indexed like `rows`.

- [ ] **Step 1: Write the failing tests**

`tests/signals/test_gates.py`:

```python
import numpy as np
import pandas as pd
import pytest

from analyzer.config import load_rules
from analyzer.features.quality import QualityFeatures
from analyzer.features.value import ValueFeatures
from analyzer.signals.gates import pick, undervalued, value_trap

RULES = load_rules()
IDX = pd.bdate_range("2024-01-01", periods=1)


def _frame(**cols):
    return pd.DataFrame({k: [v] for k, v in cols.items()}, index=IDX, dtype="float64")


def _quality(**over):
    base = {n: _frame(A=np.nan, B=np.nan, C=np.nan) for n in QualityFeatures.__dataclass_fields__}
    base["revenue_decline_2q"] = pd.DataFrame(False, index=IDX, columns=["A", "B", "C"])
    base.update(over)
    return QualityFeatures(**base)


VARIANTS = pd.Series({"A": "standard", "B": "financials", "C": "reit"})


def test_margin_rule_applies_to_standard_and_reit_only():
    q = _quality(margin_change=_frame(A=-0.10, B=-0.10, C=-0.02),
                 revenue_growth=_frame(A=0.1, B=0.1, C=0.1),
                 revenue_q_growth=_frame(A=0.1, B=0.1, C=0.1))
    t = value_trap(q, VARIANTS, RULES)
    assert t.fails.iloc[0].tolist() == [True, False, False]


def test_leverage_cap_differs_by_variant():
    q = _quality(nd_ebitda=_frame(A=5.0, B=50.0, C=5.0))
    t = value_trap(q, VARIANTS, RULES)
    assert t.fails.iloc[0].tolist() == [True, False, False]     # REIT cap 7, financials none


def test_unknown_inputs_skip_the_rule_and_are_counted():
    q = _quality()
    t = value_trap(q, VARIANTS, RULES)
    assert not t.fails.any().any()
    assert t.missing.iloc[0].tolist() == [4, 2, 3]   # standard: revenue, margin, fcf, leverage
    assert t.unknown["fcf"].iloc[0].tolist() == [True, False, False]


def test_revenue_rule_needs_ttm_and_two_quarters_down():
    q = _quality(revenue_growth=_frame(A=-0.05, B=0.0, C=0.0),
                 revenue_q_growth=_frame(A=-0.05, B=0.0, C=0.0),
                 revenue_decline_2q=pd.DataFrame([[True, True, False]], index=IDX, columns=list("ABC")))
    assert value_trap(q, VARIANTS, RULES).fails.iloc[0].tolist() == [True, False, False]


def test_guidance_tone_is_not_built_yet():
    import dataclasses
    rules = dataclasses.replace(RULES, gates=dataclasses.replace(RULES.gates, guidance_tone_enabled=True))
    with pytest.raises(NotImplementedError, match="guidance tone"):
        value_trap(_quality(), VARIANTS, rules)


def test_pick_reads_cells_and_tolerates_missing_columns():
    f = pd.DataFrame({"A": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    rows = pd.DataFrame({"symbol": ["A", "Z"], "date": f.index})
    out = pick(f, rows)
    assert out[0] == 1.0 and np.isnan(out[1])


def test_undervalued_counts_metrics_and_peers():
    idx = IDX
    ratio = {"pe": _frame(A=10.0), "ev_ebitda": _frame(A=8.0), "p_fcf": _frame(A=np.nan), "ps": _frame(A=2.0)}
    pct = {"pe": _frame(A=10.0), "ev_ebitda": _frame(A=20.0), "p_fcf": _frame(A=5.0), "ps": _frame(A=40.0)}
    peer = {"pe": _frame(A=15.0), "ev_ebitda": _frame(A=10.0), "p_fcf": _frame(A=1.0), "ps": _frame(A=1.0)}
    vf = ValueFeatures(ratio, pct, pct, peer, _frame(A=1.0), _frame(A=0.0), _frame(A=1.0))
    rows = pd.DataFrame({"symbol": ["A"], "date": idx})
    u = undervalued(rows, vf, RULES)
    assert u.loc[0, "n_metrics"] == 3                 # p_fcf has no ratio
    assert u.loc[0, "median_pct"] == 20.0
    assert u.loc[0, "cheaper"] == 2
    assert u.loc[0, "gate1"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/signals/test_gates.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `gates.py`**

`src/analyzer/signals/gates.py`:

```python
"""Who can be scored (eligibility) and buy gates 1, 3 and 4 (spec 7.1, addendum 5.4).

Gate 2 (dislocation) is in features/dislocation.py; gate 5 (a plan) is in signals/plans.py.
Gate 3 is computed on whole panels, because the position plan's thesis stop needs it on every
day of a trade, not only on the signal day."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import Rules
from analyzer.features.quality import QualityFeatures
from analyzer.features.value import METRICS, ValueFeatures
from analyzer.panel.fundamentals import FundamentalPanels
from analyzer.signals.variants import FINANCIALS, REIT, SCORED, STANDARD

TRAP_RULES = ("revenue", "margin", "roe", "fcf", "leverage")
APPLIES = {
    "revenue": (STANDARD, FINANCIALS, REIT),
    "margin": (STANDARD, REIT),
    "roe": (FINANCIALS,),
    "fcf": (STANDARD,),
    "leverage": (STANDARD, REIT),
}


def pick(frame: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    ri = frame.index.get_indexer(pd.DatetimeIndex(rows["date"]))
    ci = frame.columns.get_indexer(rows["symbol"])
    ok = (ri >= 0) & (ci >= 0)
    arr = frame.to_numpy()
    out = np.full(len(rows), np.nan, dtype="float64") if arr.dtype != bool else np.zeros(len(rows), bool)
    out[ok] = arr[ri[ok], ci[ok]]
    return out


def _by_column(variants: pd.Series, columns, allowed) -> np.ndarray:
    return np.array([variants.get(c) in allowed for c in columns])


@dataclass(frozen=True)
class Eligibility:
    eligible: pd.DataFrame
    skipped: dict[str, int]


def eligibility(member: pd.DataFrame, variants: pd.Series, f: FundamentalPanels,
                market_cap: pd.DataFrame, rules: Rules) -> Eligibility:
    cols = member.columns
    scored = _by_column(variants, cols, SCORED)[None, :]
    first = f.first_filed.reindex(cols).to_numpy(dtype="datetime64[ns]")
    dates = member.index.to_numpy(dtype="datetime64[ns]")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        age = (dates[:, None] - first[None, :]) / np.timedelta64(1, "D")
    history = age >= rules.features.history_min_days            # NaT -> False
    fin = _by_column(variants, cols, (FINANCIALS,))[None, :]
    rev = f["revenue_ttm"].reindex(columns=cols).notna().to_numpy()
    op = f["operating_income_ttm"].reindex(columns=cols).notna().to_numpy()
    ni = f["net_income_ttm"].reindex(columns=cols).notna().to_numpy()
    required = rev & np.where(fin, ni, op)
    cap = market_cap.reindex(columns=cols).notna().to_numpy()

    m = member.to_numpy(dtype=bool)
    skipped = {}
    left = m.copy()
    for reason, ok in (("variant", np.broadcast_to(scored, m.shape)), ("history", history),
                       ("required", required), ("market_cap", cap)):
        skipped[reason] = int((left & ~ok).sum())
        left &= ok
    return Eligibility(pd.DataFrame(left, index=member.index, columns=cols), skipped)


@dataclass(frozen=True)
class TrapResult:
    fails: pd.DataFrame
    missing: pd.DataFrame
    unknown: dict[str, pd.DataFrame]


def value_trap(q: QualityFeatures, variants: pd.Series, rules: Rules) -> TrapResult:
    if rules.gates.guidance_tone_enabled:
        raise NotImplementedError("guidance tone is not built yet (addendum 2.2); "
                                  "set guidance_tone_enabled = false in rules.toml")
    cols = q.revenue_growth.columns
    g = rules.gates
    caps = np.array([rules.variants.reit.net_debt_ebitda_max if variants.get(c) == REIT
                     else rules.variants.standard_net_debt_ebitda_max for c in cols])
    fired = {
        "revenue": ((q.revenue_growth < 0) & q.revenue_decline_2q.astype(bool)).to_numpy(),
        "margin": (q.margin_change < -g.margin_drop).to_numpy(),
        "roe": (q.roe_change < -g.roe_drop).to_numpy(),
        "fcf": ((q.fcf < 0) & (q.fcf < q.fcf_prev)).to_numpy(),
        "leverage": (q.nd_ebitda.to_numpy() > caps[None, :]),
    }
    known = {
        "revenue": (q.revenue_growth.notna() & q.revenue_q_growth.notna()).to_numpy(),
        "margin": q.margin_change.notna().to_numpy(),
        "roe": q.roe_change.notna().to_numpy(),
        "fcf": (q.fcf.notna() & q.fcf_prev.notna()).to_numpy(),
        "leverage": q.nd_ebitda.notna().to_numpy(),
    }
    fails = np.zeros(q.revenue_growth.shape, dtype=bool)
    missing = np.zeros(q.revenue_growth.shape, dtype=np.int8)
    unknown = {}
    for rule in TRAP_RULES:
        applies = _by_column(variants, cols, APPLIES[rule])[None, :]
        fails |= fired[rule] & known[rule] & applies
        unk = applies & ~known[rule]
        missing += unk.astype(np.int8)
        unknown[rule] = pd.DataFrame(unk, index=q.revenue_growth.index, columns=cols)
    idx = q.revenue_growth.index
    return TrapResult(pd.DataFrame(fails, index=idx, columns=cols),
                      pd.DataFrame(missing, index=idx, columns=cols), unknown)


def undervalued(rows: pd.DataFrame, vf: ValueFeatures, rules: Rules) -> pd.DataFrame:
    g = rules.gates
    present = [m for m in METRICS if m in vf.ratio]
    out = pd.DataFrame(index=rows.index)
    pcts, cheaper, avail = [], [], []
    for m in present:
        ratio, pct, peer = pick(vf.ratio[m], rows), pick(vf.pct[m], rows), pick(vf.peer[m], rows)
        ok = ~np.isnan(ratio) & ~np.isnan(pct)
        out[f"pct_{m}"] = np.where(ok, pct, np.nan)
        pcts.append(np.where(ok, pct, np.nan))
        cheaper.append(ok & ~np.isnan(peer) & (ratio < peer))
        avail.append(ok)
    for m in METRICS:
        if f"pct_{m}" not in out:
            out[f"pct_{m}"] = np.nan
    n = np.sum(avail, axis=0) if avail else np.zeros(len(rows), int)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(np.vstack(pcts), axis=0) if pcts else np.full(len(rows), np.nan)
    out["n_metrics"] = n
    out["median_pct"] = median
    out["cheaper"] = np.sum(cheaper, axis=0) if cheaper else 0
    out["gate1"] = ((out["n_metrics"] >= g.min_value_metrics)
                    & (out["median_pct"] <= g.max_value_percentile)
                    & (out["cheaper"] >= g.min_cheaper_metrics))
    return out
```

Notes for the implementer:
- In `test_unknown_inputs_skip_the_rule_and_are_counted`, the REIT (C) has three applicable rules (revenue, margin, leverage) and financials (B) two (revenue, roe).
- `pick` on a bool frame returns a bool array (missing cells `False`).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/signals/test_gates.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/signals/gates.py tests/signals/test_gates.py
git commit -m "feat: eligibility, undervalued gate and value-trap gate per variant"
```

---

### Task 18: Plans and gate 5

**Files:**
- Create: `src/analyzer/signals/plans.py`
- Test: `tests/signals/test_plans.py`

**Interfaces:**
- Consumes: `PlanRules`, `GateRules`; row columns `close, atr, window_low, high, ma, val_target`.
- Produces:
  - `valuation_target(rows, vf, metrics_by_row) -> np.ndarray`: per row, the median over the row's variant metrics of the price at which each metric is back at its own 50th percentile (`price × p50 / ratio`; for EV/EBITDA `(p50 × EBITDA − net debt) / shares`, with shares = market cap / close);
  - `make_plans(rows, plans: PlanRules, gates: GateRules) -> DataFrame` with columns `entry, zone_low, zone_high, swing_stop, swing_target, swing_rr, swing_ok, position_stop, position_target, position_rr, position_ok, gate5`, indexed like `rows`.

- [ ] **Step 1: Write the failing tests**

`tests/signals/test_plans.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.signals.plans import make_plans

R = load_rules()


def _rows(**over):
    base = dict(close=80.0, atr=2.0, window_low=78.0, high=100.0, ma=95.0, val_target=110.0)
    base.update(over)
    return pd.DataFrame([base])


def test_swing_plan_levels_and_reward_risk():
    p = make_plans(_rows(), R.plans, R.gates).iloc[0]
    assert p["entry"] == 80 and p["zone_low"] == 79 and p["zone_high"] == 81
    assert p["swing_stop"] == 78 - 4
    assert p["swing_target"] == 89                  # 78 + 0.5 * (100 - 78), below the MA of 95
    assert abs(p["swing_rr"] - 9 / 6) < 1e-9 and p["swing_ok"]
    assert p["position_stop"] == 78 - 6
    assert p["position_target"] == 100              # high (100) is closer than the value target
    assert p["gate5"]


def test_targets_below_entry_do_not_count():
    # retrace = 98 + 0.5 * (100 - 98) = 99 and the MA (90) are not above the entry of 99
    p = make_plans(_rows(close=99.0, ma=90.0, window_low=98.0, val_target=95.0), R.plans, R.gates).iloc[0]
    assert np.isnan(p["swing_target"]) and not p["swing_ok"]
    assert p["position_target"] == 100                # the value target (95) is below entry
    assert not p["gate5"]                             # R/R 1 / 7


def test_poor_reward_risk_fails_gate5():
    p = make_plans(_rows(close=80.0, atr=8.0, window_low=78.0, high=84.0, ma=83.0, val_target=np.nan),
                   R.plans, R.gates).iloc[0]
    assert not p["swing_ok"] and not p["position_ok"] and not p["gate5"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/signals/test_plans.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `plans.py`**

`src/analyzer/signals/plans.py`:

```python
"""Swing and position plans (spec 8.1) and gate 5 (a plan passes the reward/risk gate)."""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import numpy as np
import pandas as pd

from analyzer.config import GateRules, PlanRules
from analyzer.features.value import ValueFeatures
from analyzer.signals.gates import pick


def valuation_target(rows: pd.DataFrame, vf: ValueFeatures,
                     metrics_by_row: Sequence[Sequence[str]]) -> np.ndarray:
    close = rows["close"].to_numpy(dtype="float64")
    shares = pick(vf.market_cap, rows) / close
    ebitda, nd = pick(vf.ebitda, rows), pick(vf.net_debt, rows)
    targets = {}
    for m in vf.ratio:
        ratio, p50 = pick(vf.ratio[m], rows), pick(vf.p50[m], rows)
        with np.errstate(all="ignore"):
            if m == "ev_ebitda":
                targets[m] = (p50 * ebitda - nd) / shares
            else:
                targets[m] = close * p50 / ratio
    out = np.full(len(rows), np.nan)
    for i, metrics in enumerate(metrics_by_row):
        vals = [targets[m][i] for m in metrics if m in targets and np.isfinite(targets[m][i])]
        if vals:
            out[i] = float(np.median(vals))
    return out


def _closest_above(entry: np.ndarray, *levels: np.ndarray) -> np.ndarray:
    stacked = np.vstack(levels)
    above = np.where(stacked > entry[None, :], stacked, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmin(above, axis=0)


def make_plans(rows: pd.DataFrame, plans: PlanRules, gates: GateRules) -> pd.DataFrame:
    entry = rows["close"].to_numpy(dtype="float64")
    atr = rows["atr"].to_numpy(dtype="float64")
    low = rows["window_low"].to_numpy(dtype="float64")
    high = rows["high"].to_numpy(dtype="float64")
    out = pd.DataFrame(index=rows.index)
    out["entry"] = entry
    out["zone_low"] = entry - plans.entry_zone_atr * atr
    out["zone_high"] = entry + plans.entry_zone_atr * atr

    retrace = low + plans.swing_retrace * (high - low)
    out["swing_stop"] = low - plans.swing_stop_atr * atr
    out["swing_target"] = _closest_above(entry, retrace, rows["ma"].to_numpy(dtype="float64"))
    out["position_stop"] = low - plans.position_stop_atr * atr
    out["position_target"] = _closest_above(entry, high, rows["val_target"].to_numpy(dtype="float64"))
    for plan in ("swing", "position"):
        with np.errstate(all="ignore"):
            rr = (out[f"{plan}_target"] - entry) / (entry - out[f"{plan}_stop"])
        out[f"{plan}_rr"] = rr
        out[f"{plan}_ok"] = (rr >= gates.min_reward_risk).fillna(False).astype(bool)
    out["gate5"] = out["swing_ok"] | out["position_ok"]
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/signals/test_plans.py -v`
Expected: all PASS. In the first test the position target is min(100, 110) = 100 and its R/R is 20 / 8 = 2.5.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/signals/plans.py tests/signals/test_plans.py
git commit -m "feat: swing and position plans with the reward/risk gate"
```

---

### Task 19: Score, tiers and reason strings

**Files:**
- Create: `src/analyzer/signals/score.py`
- Create: `src/analyzer/signals/reasons.py`
- Test: `tests/signals/test_score.py`

**Interfaces:**
- Consumes: `Rules`; `balance_score` (Task 10); `VARIANT_METRICS`, `LABELS` (Task 9); `HOT`, `COLD` (Task 13). Row columns: `variant, median_pct, cheaper, n_metrics, correction, industry, accumulation, divergence, reclaimed, insider_cluster, growth_score, margin_score, roe_score, fcf_score, nd_ebitda`.
- Produces:
  - `score(rows, rules) -> DataFrame` with columns `c_value, c_quality, c_dislocation, c_flow, c_technical, score, tier`, indexed like `rows`;
  - `reasons(row: Mapping, rules) -> list[str]` (row columns as above plus `pct_<metric>`, `drawdown`, `gap`, `ref_etf`, `buyers`, `executive_buy`, `missing_rules`, `annual`, `fallback`).

- [ ] **Step 1: Write the failing tests**

`tests/signals/test_score.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.signals.reasons import reasons
from analyzer.signals.score import score

R = load_rules()


def _row(**over):
    base = dict(variant="standard", median_pct=15.0, cheaper=2, n_metrics=4, correction=False,
                industry=1.0, accumulation=0.5, divergence=True, reclaimed=False,
                insider_cluster=False, growth_score=1.0, margin_score=0.5, roe_score=np.nan,
                fcf_score=1.0, nd_ebitda=0.5)
    base.update(over)
    return base


def test_components_and_total():
    s = score(pd.DataFrame([_row()]), R).iloc[0]
    assert s["c_value"] == 0.5 * 0.5 + 0.5 * 0.5            # (30-15)/30 and 2 of 4 cheaper
    assert s["c_quality"] == (1.0 + 0.5 + 1.0 + 1.0) / 4    # balance 1.0 at nd/ebitda 0.5
    assert s["c_dislocation"] == 0.7
    assert s["c_flow"] == 0.5 * 1.0 + 0.5 * 0.5
    assert s["c_technical"] == 0.5
    expected = 30 * 0.5 + 30 * 0.875 + 15 * 0.7 + 15 * 0.75 + 10 * 0.5
    assert abs(s["score"] - expected) < 1e-9
    assert s["tier"] == "B"


def test_financials_quality_uses_growth_and_roe_only():
    s = score(pd.DataFrame([_row(variant="financials", roe_score=0.0, margin_score=1.0,
                                 fcf_score=1.0)]), R).iloc[0]
    assert s["c_quality"] == 0.5


def test_insider_bonus_is_capped_at_100():
    row = _row(median_pct=0.0, cheaper=4, correction=True, reclaimed=True, accumulation=1.0,
               margin_score=1.0, insider_cluster=True)
    s = score(pd.DataFrame([row]), R).iloc[0]
    assert s["score"] == 100 and s["tier"] == "A"


def test_reasons_mention_value_drop_and_skipped_rules():
    row = _row(pct_pe=12.0, pct_ev_ebitda=np.nan, pct_p_fcf=20.0, pct_ps=40.0, drawdown=0.22,
               gap=-0.15, ref_etf="SMH", buyers=0, executive_buy=False, missing_rules="fcf",
               annual=True, fallback=False)
    text = reasons(row, R)
    assert "P/E at 12th percentile of its 5-year range" in text
    assert any(r.startswith("Down 22% from its 63-day high") for r in text)
    assert "Value-trap rule skipped (no data): fcf" in text
    assert "TTM from the annual report (quarters missing)" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/signals/test_score.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `score.py` and `reasons.py`**

`src/analyzer/signals/score.py`:

```python
"""Score (0-100, ranking only) and tiers (spec 7.3). Weights are fixed, never fitted."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from analyzer.config import Rules
from analyzer.features.flow import COLD, HOT
from analyzer.features.quality import balance_score
from analyzer.signals.variants import FINANCIALS, REIT

QUALITY_PARTS = {  # guidance tone is left out of the first run (addendum 2.3)
    "standard": ("growth_score", "margin_score", "fcf_score", "balance"),
    FINANCIALS: ("growth_score", "roe_score"),
    REIT: ("growth_score", "margin_score", "balance"),
}


def _quality(rows: pd.DataFrame, rules: Rules) -> np.ndarray:
    nd = pd.DataFrame({"x": rows["nd_ebitda"].to_numpy(dtype="float64")})
    balance = np.where(rows["variant"] == REIT,
                       balance_score(nd, rules.variants.reit.net_debt_ebitda_max)["x"],
                       balance_score(nd, rules.variants.standard_net_debt_ebitda_max)["x"])
    parts = {"growth_score": rows["growth_score"], "margin_score": rows["margin_score"],
             "roe_score": rows["roe_score"], "fcf_score": rows["fcf_score"], "balance": balance}
    out = np.full(len(rows), np.nan)
    for i, variant in enumerate(rows["variant"]):
        names = QUALITY_PARTS.get(variant, ())
        vals = [float(np.asarray(parts[n])[i]) for n in names]
        vals = [v for v in vals if not np.isnan(v)]
        out[i] = float(np.mean(vals)) if vals else 0.0
    return out


def score(rows: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    s, g = rules.score, rules.gates
    out = pd.DataFrame(index=rows.index)
    depth = ((g.max_value_percentile - rows["median_pct"]) / g.max_value_percentile).clip(0, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        share = (rows["cheaper"] / rows["n_metrics"]).fillna(0)
    out["c_value"] = 0.5 * depth.fillna(0) + 0.5 * share
    out["c_quality"] = _quality(rows, rules)
    out["c_dislocation"] = np.where(rows["correction"].astype(bool), s.dislocation_correction_drift,
                                    s.dislocation_idiosyncratic_drift)
    industry = rows["industry"].to_numpy(dtype="float64")
    industry_score = np.where(industry == HOT, s.flow_hot,
                              np.where(industry == COLD, s.flow_cold, s.flow_neutral))
    out["c_flow"] = 0.5 * industry_score + 0.5 * rows["accumulation"].fillna(0).to_numpy()
    out["c_technical"] = (rows["divergence"].astype(float) + rows["reclaimed"].astype(float)) / 2
    total = (s.weight_value * out["c_value"] + s.weight_quality * out["c_quality"]
             + s.weight_dislocation * out["c_dislocation"] + s.weight_flow * out["c_flow"]
             + s.weight_technical * out["c_technical"]
             + s.insider_bonus * rows["insider_cluster"].astype(float))
    out["score"] = total.clip(upper=100)
    out["tier"] = np.where(out["score"] >= s.tier_a, "A", np.where(out["score"] >= s.tier_b, "B", "C"))
    return out
```

`src/analyzer/signals/reasons.py`:

```python
"""Reason strings: one per feature that fires (spec 7.6)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from analyzer.config import Rules
from analyzer.features.flow import COLD, HOT
from analyzer.features.value import LABELS, VARIANT_METRICS


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def reasons(row: Mapping, rules: Rules) -> list[str]:
    out = []
    for m in VARIANT_METRICS.get(row["variant"], ()):
        pct = _num(row.get(f"pct_{m}"))
        if not np.isnan(pct):
            out.append(f"{LABELS[m]} at {pct:.0f}th percentile of its 5-year range")
    out.append(f"Cheaper than peers on {int(row['cheaper'])} of {int(row['n_metrics'])} metrics")
    out.append(f"Down {_num(row['drawdown']):.0%} from its 63-day high, "
               f"{_num(row['gap']):+.0%} vs {row['ref_etf']}")
    out.append("Market correction, drift" if row["correction"] else "Idiosyncratic drop, drift")
    industry = _num(row["industry"])
    if industry == HOT:
        out.append(f"Industry hot ({row['ref_etf']})")
    elif industry == COLD:
        out.append(f"Industry cold ({row['ref_etf']})")
    if row["divergence"]:
        out.append("Bullish RSI divergence")
    if row["reclaimed"]:
        out.append("Reclaimed support")
    if row["insider_cluster"]:
        if row.get("executive_buy"):
            out.append("Insider buying: CEO/CFO purchase")
        else:
            out.append(f"Insider buying: {int(row.get('buyers', 0))} insiders")
    if row.get("missing_rules"):
        out.append(f"Value-trap rule skipped (no data): {row['missing_rules']}")
    if row.get("annual"):
        out.append("TTM from the annual report (quarters missing)")
    if row.get("fallback"):
        out.append("Uses fallback tags (hindsight-chosen; addendum 5.2)")
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/signals/test_score.py -v`
Expected: all PASS. `test_insider_bonus_is_capped_at_100`: all components 1.0 except technical (divergence and reclaimed both True → 1.0) gives 100 before the bonus.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/signals/score.py src/analyzer/signals/reasons.py tests/signals/test_score.py
git commit -m "feat: setup score, tiers and reason strings"
```

---
### Task 20: `evaluate()` and the synthetic fixture database

**Files:**
- Create: `src/analyzer/signals/evaluate.py`
- Create: `tests/fixture_db.py`
- Test: `tests/signals/test_evaluate.py`

**Interfaces:**
- Consumes: everything from Tasks 5–19.
- Produces:
  - `Panels` (frozen dataclass: `stocks: PricePanels`, `etfs: PricePanels`, `member: DataFrame[bool]` (index membership and liquidity), `entities: DataFrame`, `fundamentals: FundamentalPanels`, `purchases: DataFrame`, `macro: DataFrame`); property `dates`; method `upto(end) -> Panels`;
  - `load_panels(conn, rules, end: date | None = None) -> Panels` (dates from `rules.research.panel_start` to `end`, default today);
  - `Evaluation` (frozen dataclass: `rows: DataFrame`, `dislocated_all: DataFrame[symbol, date, pos]`, `member`, `eligible`, `trap_fails: DataFrame[bool]`, `skipped: dict[str, int]`, `variants: Series`, `regime: Series`);
  - `evaluate(panels, rules) -> Evaluation`. `rows` has one row per eligible dislocated ticker-day, sorted by date then symbol, with columns: `symbol, date, pos, variant, cik, ref_etf, sector, close, atr, ma, high, window_low, drawdown, gap, since_high, window_start, shock, correction, divergence, reclaimed, industry, accumulation, trap_fail, missing, missing_rules, annual, fallback, growth_score, margin_score, roe_score, fcf_score, nd_ebitda, market_cap, pct_<metric>…, n_metrics, median_pct, cheaper, gate1, val_target, buyers, executive_buy, insider_cluster, entry, zone_low, zone_high, swing_stop, swing_target, swing_rr, swing_ok, position_stop, position_target, position_rr, position_ok, gate5, gate3, gate4, setup, c_value, c_quality, c_dislocation, c_flow, c_technical, score, tier, regime`;
  - test helper `build_fixture_db(path) -> sqlite3.Connection` and constants `DIP`, `PEERS`, `DROP_START` in `tests/fixture_db.py`; `fixture_rules() -> Rules` (`panel_start = "2012-01-01"`).

- [ ] **Step 1: Write the fixture database**

`tests/fixture_db.py`:

```python
"""A small synthetic database for signal and research tests.

Twelve technology stocks (SIC 3674, one peer group), SPY and XLK, 2012-2016, with quarterly
facts from Q4 2010. Every company grows revenue 2% a quarter at stable margins. DIP's price
drifts 25% lower over 40 trading days from 2015-09-01 while its business keeps growing, so it
should become a setup; the peers only wiggle by about 1%."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analyzer.config import Rules, load_rules
from analyzer.store.db import connect, migrate

DIP = "DIP"
PEERS = [f"P{i:02d}" for i in range(11)]
STOCKS = [DIP, *PEERS]
DATES = pd.bdate_range("2012-01-02", "2016-12-30")
DROP_START = pd.Timestamp("2015-09-01")
DROP_DAYS = 40
FILING_LAG_DAYS = 40


def fixture_rules() -> Rules:
    rules = load_rules()
    return dataclasses.replace(
        rules, research=dataclasses.replace(rules.research, panel_start="2012-01-01"))


def _closes(i: int, symbol: str) -> np.ndarray:
    t = np.arange(len(DATES))
    base = 100 * (1 + 0.0001 * t) * (1 + 0.01 * np.sin(t / 15 + i))
    if symbol == DIP:
        k = DATES.searchsorted(DROP_START)
        factor = np.ones(len(DATES))
        factor[k:k + DROP_DAYS] = np.linspace(1.0, 0.75, DROP_DAYS)
        factor[k + DROP_DAYS:] = 0.75
        base = base * factor
    return base


def _price_rows(symbol: str, closes: np.ndarray) -> list[tuple]:
    return [(symbol, d.date().isoformat(), c, c * 1.01, c * 0.99, c, c, 1_000_000.0, "fixture")
            for d, c in zip(DATES, closes, strict=True)]


def _fact_rows(cik: int) -> list[tuple]:
    rows = []
    ends = pd.date_range("2010-12-31", "2016-09-30", freq="QE")
    for k, end in enumerate(ends):
        start = (end - pd.offsets.QuarterEnd() + pd.Timedelta(days=1)).date().isoformat()
        filed = (end + pd.Timedelta(days=FILING_LAG_DAYS)).date().isoformat()
        e = end.date().isoformat()
        rev = 1000 * 1.02 ** k
        duration = {"revenue": ("us-gaap:Revenues", rev),
                    "operating_income": ("us-gaap:OperatingIncomeLoss", 0.20 * rev),
                    "net_income": ("us-gaap:NetIncomeLoss", 0.15 * rev),
                    "eps_diluted": ("us-gaap:EarningsPerShareDiluted", 0.15 * rev / 100),
                    "operating_cash_flow": ("us-gaap:NetCashProvidedByUsedInOperatingActivities", 0.18 * rev),
                    "capex": ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 0.05 * rev),
                    "depreciation_amortization": ("us-gaap:DepreciationDepletionAndAmortization", 0.03 * rev)}
        instant = {"stockholders_equity": ("us-gaap:StockholdersEquity", 4000.0),
                   "cash": ("us-gaap:CashAndCashEquivalentsAtCarryingValue", 1000.0),
                   "long_term_debt": ("us-gaap:LongTermDebtNoncurrent", 1000.0),
                   "shares_outstanding": ("dei:EntityCommonStockSharesOutstanding", 100.0)}
        form = "10-K" if end.month == 12 else "10-Q"
        for concept, (tag, value) in duration.items():
            rows.append((cik, concept, tag, "USD", start, e, value, filed, form, f"{cik}-{e}", "companyfacts"))
        for concept, (tag, value) in instant.items():
            rows.append((cik, concept, tag, "USD", "", e, value, filed, form, f"{cik}-{e}", "companyfacts"))
    return rows


def build_fixture_db(path):
    conn = connect(path)
    migrate(conn)
    t = np.arange(len(DATES))
    prices = _price_rows("SPY", 200 * (1 + 0.0001 * t)) + _price_rows("XLK", 50 * (1 + 0.0001 * t))
    facts = []
    for i, symbol in enumerate(STOCKS):
        cik = 1000 + i
        prices += _price_rows(symbol, _closes(i, symbol))
        facts += _fact_rows(cik)
        conn.execute("INSERT INTO tickers (ticker, cik, name, gics_sector, sector_etf, updated_at) "
                     "VALUES (?, ?, ?, 'Information Technology', 'XLK', 'x')", (symbol, cik, symbol))
        conn.execute("INSERT INTO companies VALUES (?, ?, '3674', 'semis', 'x')", (cik, symbol))
        conn.execute("INSERT INTO index_membership VALUES ('sp500', ?, '2010-01-01', NULL)", (symbol,))
    with conn:
        conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)", prices)
        conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", facts)
    return conn
```

- [ ] **Step 2: Write the failing tests**

`tests/signals/test_evaluate.py`:

```python
import pandas as pd
import pytest

from analyzer.signals.evaluate import evaluate, load_panels
from tests.fixture_db import DIP, DROP_START, build_fixture_db, fixture_rules

RULES = fixture_rules()


@pytest.fixture(scope="module")
def panels(tmp_path_factory):
    conn = build_fixture_db(tmp_path_factory.mktemp("db") / "a.db")
    return load_panels(conn, RULES, end=pd.Timestamp("2016-12-30").date())


@pytest.fixture(scope="module")
def evaluation(panels):
    return evaluate(panels, RULES)


def test_only_the_dip_becomes_a_setup(evaluation):
    setups = evaluation.rows[evaluation.rows["setup"]]
    assert set(setups["symbol"]) == {DIP}
    first = setups["date"].min()
    assert DROP_START < first < DROP_START + pd.Timedelta(days=70)


def test_setup_rows_carry_reasons_inputs(evaluation):
    row = evaluation.rows[evaluation.rows["setup"]].iloc[0]
    assert row["variant"] == "standard" and row["gate1"] and row["gate5"]
    assert row["median_pct"] <= 30 and row["cheaper"] >= 2
    assert 0 <= row["score"] <= 100 and row["tier"] in {"A", "B", "C"}
    assert row["missing_rules"] == ""


def test_evaluate_is_truncation_invariant(panels, evaluation):
    first = evaluation.rows.loc[evaluation.rows["setup"], "date"].min()
    for d in (first, first + pd.Timedelta(days=14)):
        d = panels.dates[panels.dates.searchsorted(d)]
        part = evaluate(panels.upto(d), RULES).rows
        a = evaluation.rows[evaluation.rows["date"] == d].reset_index(drop=True)
        b = part[part["date"] == d].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_skipped_counts_are_reported(evaluation):
    assert set(evaluation.skipped) == {"variant", "history", "required", "market_cap"}
    assert evaluation.skipped["history"] > 0          # 2012-2014: less than 3 years of filings
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/signals/test_evaluate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'analyzer.signals.evaluate'`.

- [ ] **Step 4: Write `evaluate.py`**

`src/analyzer/signals/evaluate.py`:

```python
"""The one entry point shared by research and the future daily scan: panels in, scored rows
out. The scan will call `evaluate(load_panels(...))` and keep the last date (addendum 4.4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from analyzer.config import Rules
from analyzer.features.dislocation import dislocation
from analyzer.features.flow import flow
from analyzer.features.insider import cluster_buying, load_purchases
from analyzer.features.quality import quality_features
from analyzer.features.regime import load_macro, regime
from analyzer.features.technical import technical
from analyzer.features.value import VARIANT_METRICS, value_features
from analyzer.panel.fundamentals import FundamentalPanels, load_fundamental_panels
from analyzer.panel.prices import PricePanels, load_price_panels, trading_dates
from analyzer.panel.universe import liquidity_panel, load_entities, membership_panel, symbol_map
from analyzer.signals.gates import TRAP_RULES, eligibility, pick, undervalued, value_trap
from analyzer.signals.plans import make_plans, valuation_target
from analyzer.signals.score import score
from analyzer.signals.variants import columns_for_metrics, variant_series
from analyzer.universe.industry import ALL_REFERENCE_ETFS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Panels:
    stocks: PricePanels
    etfs: PricePanels
    member: pd.DataFrame
    entities: pd.DataFrame
    fundamentals: FundamentalPanels
    purchases: pd.DataFrame
    macro: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.stocks.dates

    def upto(self, end) -> Panels:
        end = pd.Timestamp(end)
        return Panels(self.stocks.upto(end), self.etfs.upto(end), self.member.loc[:end],
                      self.entities, self.fundamentals.upto(end),
                      self.purchases[self.purchases["filing_date"] <= end], self.macro.loc[:end])


def load_panels(conn, rules: Rules, end: date | None = None) -> Panels:
    dates = trading_dates(conn, date.fromisoformat(rules.research.panel_start), end or date.today())
    if len(dates) == 0:
        raise ValueError("no SPY prices in the requested range; run `analyzer update` first")
    symbols_of = symbol_map(conn)
    symbols = sorted(set(symbols_of.values()))
    log.info("panels: %d dates x %d symbols", len(dates), len(symbols))
    stocks = load_price_panels(conn, symbols, dates)
    etfs = load_price_panels(conn, ALL_REFERENCE_ETFS, dates)
    member = (membership_panel(conn, dates, symbols_of).reindex(columns=symbols, fill_value=False)
              & liquidity_panel(stocks.close, stocks.volume, rules.universe))
    entities = load_entities(conn, symbols)
    fundamentals = load_fundamental_panels(conn, entities, dates, rules.features)
    return Panels(stocks, etfs, member, entities, fundamentals, load_purchases(conn),
                  load_macro(conn, dates))


@dataclass(frozen=True)
class Evaluation:
    rows: pd.DataFrame
    dislocated_all: pd.DataFrame
    member: pd.DataFrame
    eligible: pd.DataFrame
    trap_fails: pd.DataFrame
    skipped: dict[str, int]
    variants: pd.Series
    regime: pd.Series


def _long(mask: pd.DataFrame) -> pd.DataFrame:
    ri, ci = np.nonzero(mask.to_numpy(dtype=bool))
    rows = pd.DataFrame({"symbol": mask.columns[ci], "date": mask.index[ri], "pos": ri})
    return rows.sort_values(["date", "symbol"]).reset_index(drop=True)


def evaluate(p: Panels, rules: Rules) -> Evaluation:
    f, feat = p.fundamentals, rules.features
    variants = variant_series(p.entities, f, rules.variants)
    ref_of = p.entities["ref_etf"].to_dict()
    vf = value_features(p.stocks.close, f, p.member, p.entities, columns_for_metrics(variants), feat)
    qf = quality_features(f, vf.net_debt, feat, rules.score)
    elig = eligibility(p.member, variants, f, vf.market_cap, rules)
    disl = dislocation(p.stocks, p.etfs.adj_close, p.etfs.close["SPY"], ref_of, feat, rules.gates)
    tech = technical(p.stocks, feat)
    fl = flow(p.stocks, p.etfs, p.etfs.adj_close["SPY"], elig.eligible, ref_of, disl.since_high, feat)
    trap = value_trap(qf, variants, rules)
    reg = regime(p.macro)

    rows = _long(elig.eligible & disl.dislocated)
    columns = {
        "close": p.stocks.close, "atr": tech.atr, "ma": tech.ma, "high": disl.high,
        "window_low": disl.window_low, "drawdown": disl.drawdown, "gap": disl.gap,
        "since_high": disl.since_high, "shock": disl.shock, "correction": disl.correction,
        "divergence": tech.divergence, "reclaimed": tech.reclaimed, "industry": fl.industry,
        "accumulation": fl.accumulation, "trap_fail": trap.fails, "missing": trap.missing,
        "annual": f["annual"], "fallback": f["fallback"], "growth_score": qf.growth_score,
        "margin_score": qf.margin_score, "roe_score": qf.roe_score, "fcf_score": qf.fcf_score,
        "nd_ebitda": qf.nd_ebitda, "market_cap": vf.market_cap,
    }
    for name, frame in columns.items():
        rows[name] = pick(frame, rows)
    unknown = {rule: pick(trap.unknown[rule], rows) for rule in TRAP_RULES}
    rows["missing_rules"] = [",".join(r for r in TRAP_RULES if unknown[r][i]) for i in range(len(rows))]
    rows["variant"] = rows["symbol"].map(variants)
    rows["cik"] = rows["symbol"].map(p.entities["cik"])
    rows["ref_etf"] = rows["symbol"].map(p.entities["ref_etf"])
    rows["sector"] = rows["symbol"].map(p.entities["sector"])
    start = (rows["pos"] - rows["since_high"].fillna(0)).astype(int)
    rows["window_start"] = p.dates[start.to_numpy()] if len(rows) else pd.Series(dtype="datetime64[ns]")

    rows = pd.concat([rows, undervalued(rows, vf, rules)], axis=1)
    rows["val_target"] = valuation_target(rows, vf, [VARIANT_METRICS.get(v, ()) for v in rows["variant"]])
    insider = cluster_buying(rows, p.purchases, feat)
    rows["buyers"] = insider["buyers"]
    rows["executive_buy"] = insider["executive_buy"]
    rows["insider_cluster"] = insider["cluster"]
    rows = pd.concat([rows, make_plans(rows, rules.plans, rules.gates)], axis=1)
    rows["gate3"] = ~rows["trap_fail"].astype(bool)
    rows["gate4"] = ~rows["shock"].astype(bool)
    rows["setup"] = rows["gate1"] & rows["gate3"] & rows["gate4"] & rows["gate5"]
    rows = pd.concat([rows, score(rows, rules)], axis=1)
    rows["regime"] = reg.reindex(pd.DatetimeIndex(rows["date"])).to_numpy()
    return Evaluation(rows=rows, dislocated_all=_long(p.member & disl.dislocated),
                      member=p.member, eligible=elig.eligible, trap_fails=trap.fails,
                      skipped=elig.skipped, variants=variants, regime=reg)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/signals/test_evaluate.py -v`
Expected: all PASS. If `test_only_the_dip_becomes_a_setup` finds no setup, print the DIP rows around 2015-10 (`evaluation.rows.query("symbol == 'DIP'")[["date","gate1","gate3","gate4","gate5","median_pct","cheaper","swing_rr","position_rr"]]`) and fix the code, not the fixture: the fixture is built so that every gate passes (P/E percentile near 0 after the drop; 25% cheaper than eleven identical peers; no debt; growing revenue; drift drop; R/R about 1.7 swing and 2.6 position).

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/signals/evaluate.py tests/fixture_db.py tests/signals/test_evaluate.py
git commit -m "feat: evaluate() joins panels, features and gates into scored rows"
```

---

### Task 21: Events, cooldown and periods

**Files:**
- Create: `src/analyzer/research/__init__.py` (empty)
- Create: `src/analyzer/research/events.py`
- Create: `tests/research/__init__.py` (empty)
- Test: `tests/research/test_events.py`

**Interfaces:**
- Consumes: `Evaluation.rows`, `Evaluation.dislocated_all` (Task 20); `ResearchRules`.
- Produces:
  - `GROUP_SETUP = "setup"` (group 3), `GROUP_DISLOCATED = "dislocated"` (group 2: eligible pool, gate 2), `GROUP_DISLOCATED_ALL = "dislocated_all"` (secondary line: every dislocated universe member, scored or not);
  - `cooldown_mask(rows, days) -> np.ndarray[bool]` (aligned with `rows`; keeps a row when it is the ticker's first row or at least `days` positions after the last kept row);
  - `select_events(evaluation, days) -> DataFrame` (rows of the three groups with a `group` column);
  - `assign_period(events, dates, rules) -> Series[str]` with values `in_sample | purged | oos | oos_incomplete | before_start`.

- [ ] **Step 1: Write the failing tests**

`tests/research/test_events.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.research.events import assign_period, cooldown_mask

R = load_rules().research


def test_cooldown_keeps_first_day_then_waits():
    rows = pd.DataFrame({"symbol": ["A"] * 50 + ["B"] * 2, "pos": list(range(50)) + [3, 4]})
    keep = cooldown_mask(rows, 42)
    assert rows[keep]["pos"].tolist() == [0, 42, 3]


def test_setups_get_their_own_cooldown():
    rows = pd.DataFrame({"symbol": ["A"] * 20, "pos": range(20), "setup": [False] * 10 + [True] * 10})
    g2 = rows[cooldown_mask(rows, 42)]
    setups = rows[rows["setup"]]
    g3 = setups[cooldown_mask(setups, 42)]
    assert g2["pos"].tolist() == [0] and g3["pos"].tolist() == [10]


def test_periods_and_purge():
    dates = pd.bdate_range("2013-01-01", "2023-06-30")
    pos = lambda d: dates.searchsorted(pd.Timestamp(d))
    ev = pd.DataFrame({"pos": [pos("2013-06-03"), pos("2015-03-02"), pos("2021-11-01"),
                               pos("2022-03-01"), len(dates) - 10]})
    assert assign_period(ev, dates, load_rules()).tolist() == [
        "before_start", "in_sample", "purged", "oos", "oos_incomplete"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/research/test_events.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `events.py`**

`src/analyzer/research/events.py`:

```python
"""Events: the first qualifying day of a ticker in a group, then a cooldown (addendum 6.2), and
the period each event belongs to (addendum 6.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analyzer.config import Rules

GROUP_SETUP = "setup"
GROUP_DISLOCATED = "dislocated"
GROUP_DISLOCATED_ALL = "dislocated_all"


def cooldown_mask(rows: pd.DataFrame, days: int) -> np.ndarray:
    keep = np.zeros(len(rows), dtype=bool)
    symbols = rows["symbol"].to_numpy()
    pos = rows["pos"].to_numpy()
    last: dict[str, int] = {}
    for i in np.lexsort((pos, symbols)):
        s, p = symbols[i], pos[i]
        if s not in last or p - last[s] >= days:
            keep[i] = True
            last[s] = p
    return keep


def select_events(evaluation, days: int) -> pd.DataFrame:
    rows = evaluation.rows
    setups = rows[rows["setup"]]
    parts = [
        rows[cooldown_mask(rows, days)].assign(group=GROUP_DISLOCATED),
        setups[cooldown_mask(setups, days)].assign(group=GROUP_SETUP),
        evaluation.dislocated_all[cooldown_mask(evaluation.dislocated_all, days)]
        .assign(group=GROUP_DISLOCATED_ALL),
    ]
    return pd.concat(parts, ignore_index=True)


def assign_period(events: pd.DataFrame, dates: pd.DatetimeIndex, rules: Rules) -> pd.Series:
    r = rules.research
    start = dates.searchsorted(pd.Timestamp(r.start))
    oos = dates.searchsorted(pd.Timestamp(r.oos_start))
    last = len(dates) - 1
    pos = events["pos"].to_numpy()
    before_oos = (pos >= start) & (pos < oos)
    in_sample = before_oos & (pos + r.purge_horizon < min(oos, last + 1))
    oos_full = (pos >= oos) & (pos + r.go_horizon <= last)
    labels = np.select(
        [in_sample, before_oos, oos_full, pos >= oos],
        ["in_sample", "purged", "oos", "oos_incomplete"], default="before_start")
    return pd.Series(labels, index=events.index)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/research/test_events.py -v`
Expected: all PASS. 2021-11-01 is 126 trading days or fewer before 2022-01-01, so it is purged.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/research tests/research
git commit -m "feat: research events with per-group cooldown, split and purge"
```

---

### Task 22: Outcomes and plan simulation

**Files:**
- Create: `src/analyzer/research/outcomes.py`
- Test: `tests/research/test_outcomes.py`

**Interfaces:**
- Consumes: `PricePanels`; events with `symbol, pos`, and for plans `zone_low, zone_high, swing_stop, swing_target, position_stop, position_target`; `trap_fails: DataFrame[bool]`; `PlanRules`.
- Produces:
  - `forward_returns(events, stocks, etfs, ref_of, horizons, mae_horizon) -> DataFrame` with `entry_price` (the adjusted next open; the plan's own `entry` column is the signal-day close) and, per horizon h, `ret_h, spy_h, xs_spy_h, xs_ref_h`, plus `mae` (min adjusted low from entry to `pos + mae_horizon`, over entry, minus 1), indexed like `events`;
  - `simulate_plan(open_, close, trap, pos, plan_row, plan, time_stop) -> tuple[str, float, float, int]` (outcome in `target | stop | thesis | time | open | no_fill | no_data`, exit price, R multiple, days held);
  - `simulate_plans(events, stocks, trap_fails, plans) -> DataFrame` with `swing_outcome, swing_r, swing_days, position_outcome, position_r, position_days`.

- [ ] **Step 1: Write the failing tests**

`tests/research/test_outcomes.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.research.outcomes import forward_returns, simulate_plan
from tests.panel_fixtures import make_price_panels

P = load_rules().plans


def _panels(stock, spy):
    idx = pd.bdate_range("2024-01-01", periods=len(stock))
    s = make_price_panels(pd.DataFrame({"A": stock}, index=idx))
    e = make_price_panels(pd.DataFrame({"SPY": spy, "XLK": spy}, index=idx))
    return s, e


def test_returns_start_at_the_next_open():
    s, e = _panels([10.0, 11, 12, 13, 14, 15], [100.0, 100, 101, 102, 103, 104])
    ev = pd.DataFrame({"symbol": ["A"], "pos": [0]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=3).iloc[0]
    assert out["entry_price"] == 11.0
    assert abs(out["ret_2"] - (12 / 11 - 1)) < 1e-12
    assert abs(out["xs_spy_2"] - ((12 / 11 - 1) - (101 / 100 - 1))) < 1e-12
    assert out["mae"] == 0.0


def test_event_on_last_day_has_no_outcome():
    s, e = _panels([10.0, 11, 12], [100.0, 100, 100])
    ev = pd.DataFrame({"symbol": ["A", "A"], "pos": [2, 1]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=2)
    assert np.isnan(out.loc[0, "entry_price"]) and np.isnan(out.loc[0, "ret_2"])
    assert out.loc[1, "entry_price"] == 12.0 and np.isnan(out.loc[1, "ret_2"])   # window incomplete


def _plan(**over):
    base = dict(zone_low=9.0, zone_high=11.0, swing_stop=8.0, swing_target=13.0)
    base.update(over)
    return base


def test_plan_hits_target():
    o = c = np.array([10.0, 10, 11, 13.5, 9])
    assert simulate_plan(o, c, np.zeros(5, bool), 0, _plan(), "swing", 20)[:2] == ("target", 13.5)


def test_plan_hits_stop_and_reports_r():
    o = c = np.array([10.0, 10, 7.5])
    outcome, price, r, days = simulate_plan(o, c, np.zeros(3, bool), 0, _plan(), "swing", 20)
    assert outcome == "stop" and price == 7.5 and abs(r - (-2.5 / 2)) < 1e-12 and days == 2


def test_gap_outside_zone_is_no_fill():
    o = np.array([10.0, 12.0, 12.0])
    assert simulate_plan(o, o, np.zeros(3, bool), 0, _plan(), "swing", 20)[0] == "no_fill"


def test_time_stop_and_open_trade():
    o = c = np.full(30, 10.0)
    assert simulate_plan(o, c, np.zeros(30, bool), 0, _plan(), "swing", 20)[0] == "time"
    assert simulate_plan(o[:10], c[:10], np.zeros(10, bool), 0, _plan(), "swing", 20)[0] == "open"


def test_thesis_stop_exits_next_open_for_position_plans_only():
    o = c = np.array([10.0, 10, 10, 10.5, 10])
    trap = np.array([False, False, True, True, True])
    plan = _plan(position_stop=7.0, position_target=20.0)
    assert simulate_plan(o, c, trap, 0, plan, "position", 126)[:2] == ("thesis", 10.5)
    assert simulate_plan(o, c, trap, 0, plan, "swing", 20)[0] == "open"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/research/test_outcomes.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `outcomes.py`**

`src/analyzer/research/outcomes.py`:

```python
"""What happened after each event: forward returns from the next open (addendum 6.2) and
plan outcomes on daily closes (addendum 6.4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from analyzer.config import PlanRules
from analyzer.panel.prices import PricePanels


def _at(arr: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0)
    out = np.full(len(rows), np.nan)
    out[ok] = arr[rows[ok], cols[ok]]
    return out


def forward_returns(events: pd.DataFrame, stocks: PricePanels, etfs: PricePanels,
                    ref_of: Mapping[str, str], horizons: Sequence[int], mae_horizon: int) -> pd.DataFrame:
    n = len(stocks.dates)
    pos = events["pos"].to_numpy(dtype=int)
    entry_row = pos + 1
    ci = stocks.close.columns.get_indexer(events["symbol"])
    spy_ci = np.full(len(events), etfs.close.columns.get_loc("SPY"))
    ref_ci = etfs.close.columns.get_indexer([ref_of.get(s, "SPY") for s in events["symbol"]])
    s_open, s_close = stocks.adj_open.to_numpy(), stocks.adj_close.to_numpy()
    e_open, e_close = etfs.adj_open.to_numpy(), etfs.adj_close.to_numpy()
    entry = _at(s_open, entry_row, ci)
    spy_entry, ref_entry = _at(e_open, entry_row, spy_ci), _at(e_open, entry_row, ref_ci)
    out = pd.DataFrame(index=events.index)
    out["entry_price"] = entry
    for h in horizons:
        exit_row = pos + h
        ret = _at(s_close, exit_row, ci) / entry - 1
        spy = _at(e_close, exit_row, spy_ci) / spy_entry - 1
        ref = _at(e_close, exit_row, ref_ci) / ref_entry - 1
        out[f"ret_{h}"], out[f"spy_{h}"] = ret, spy
        out[f"xs_spy_{h}"], out[f"xs_ref_{h}"] = ret - spy, ret - ref
    adj_low = (stocks.low * stocks.adj_close / stocks.close).to_numpy()
    mae = np.full(len(events), np.nan)
    for i, (p, c) in enumerate(zip(pos, ci, strict=True)):
        last = p + mae_horizon
        if c < 0 or p + 1 >= n or last >= n:
            continue
        mae[i] = np.nanmin(adj_low[p + 1:last + 1, c]) / entry[i] - 1
    out["mae"] = mae
    return out


def simulate_plan(open_: np.ndarray, close: np.ndarray, trap: np.ndarray, pos: int,
                  plan_row: Mapping, plan: str, time_stop: int) -> tuple[str, float, float, int]:
    n = len(close)
    e = pos + 1
    if e >= n or np.isnan(open_[e]):
        return "no_data", np.nan, np.nan, 0
    entry = open_[e]
    if not plan_row["zone_low"] <= entry <= plan_row["zone_high"]:
        return "no_fill", np.nan, np.nan, 0
    stop, target = plan_row[f"{plan}_stop"], plan_row[f"{plan}_target"]
    risk = entry - stop
    last = e + time_stop - 1
    for d in range(e, min(last, n - 1) + 1):
        c = close[d]
        if np.isnan(c):
            continue
        if c <= stop:
            return "stop", c, (c - entry) / risk, d - e + 1
        if c >= target:
            return "target", c, (c - entry) / risk, d - e + 1
        if plan == "position" and trap[d] and d + 1 < n:
            x = open_[d + 1]
            return "thesis", x, (x - entry) / risk, d - e + 2
    if last <= n - 1:
        return "time", close[last], (close[last] - entry) / risk, time_stop
    return "open", close[n - 1], (close[n - 1] - entry) / risk, n - e


def simulate_plans(events: pd.DataFrame, stocks: PricePanels, trap_fails: pd.DataFrame,
                   plans: PlanRules) -> pd.DataFrame:
    out = pd.DataFrame(index=events.index)
    opens, closes = stocks.open, stocks.close
    trap = trap_fails.reindex(index=stocks.dates, columns=closes.columns).fillna(False)
    results = {f"{p}_{k}": [] for p in ("swing", "position") for k in ("outcome", "r", "days")}
    for row in events.to_dict("records"):
        s = row["symbol"]
        o, c, t = opens[s].to_numpy(), closes[s].to_numpy(), trap[s].to_numpy(dtype=bool)
        for plan, horizon in (("swing", plans.swing_time_stop), ("position", plans.position_time_stop)):
            if np.isnan(row[f"{plan}_target"]):
                outcome, r, days = "no_plan", np.nan, 0
            else:
                outcome, _price, r, days = simulate_plan(o, c, t, int(row["pos"]), row, plan, horizon)
            results[f"{plan}_outcome"].append(outcome)
            results[f"{plan}_r"].append(r)
            results[f"{plan}_days"].append(days)
    for k, v in results.items():
        out[k] = v
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/research/test_outcomes.py -v`
Expected: all PASS. In `test_thesis_stop...` the swing plan has no thesis stop and its 20-day time stop is beyond the data, so it is `open`.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/research/outcomes.py tests/research/test_outcomes.py
git commit -m "feat: forward returns, adverse excursion and plan simulation"
```

---

### Task 23: Statistics and the go/no-go verdict

**Files:**
- Create: `src/analyzer/research/stats.py`
- Test: `tests/research/test_stats.py`

**Interfaces:**
- Consumes: event frames with `date`, `ret_h`, `xs_spy_h`, `xs_ref_h`, `mae`, `score`; `Rules`.
- Produces:
  - `is_win(events, rules) -> Series[bool]` (63-day excess return vs SPY > 0 for `win_basis = "excess_spy"`; raw return > 0 for `"raw"`);
  - `bootstrap_ci(values, months, stat, samples, seed) -> tuple[float, float]` (95%, resampling whole calendar months);
  - `summarize(events, rules) -> dict` with keys `n, median_ret, median_xs_spy, median_xs_ref, win_rate, median_mae, ci_median_xs_spy, ci_win_rate, median_spy`;
  - `quintiles(setups, rules) -> DataFrame[quintile, n, median_xs_spy]`;
  - `breakdowns(events, columns, rules) -> DataFrame[column, value, n, median_xs_spy, win_rate]`;
  - `verdict(setup_summary, dislocated_summary, quintile_table, rules) -> dict` with `tests: dict[str, bool]` (keys `n`, `median_xs_spy`, `win_rate`, `beats_dislocated`, `quintiles`) and `passed: bool`.

- [ ] **Step 1: Write the failing tests**

`tests/research/test_stats.py`:

```python
import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.research.stats import bootstrap_ci, quintiles, summarize, verdict

R = load_rules()


def _events(xs, start="2022-01-03"):
    n = len(xs)
    return pd.DataFrame({"date": pd.bdate_range(start, periods=n, freq="7D"),
                         "ret_63": np.array(xs) + 0.01, "spy_63": 0.01, "xs_spy_63": xs,
                         "xs_ref_63": xs, "mae": -0.05, "score": np.linspace(50, 90, n)})


def test_summary_counts_only_complete_windows():
    ev = _events([0.1, -0.05, 0.02, np.nan])
    s = summarize(ev, R)
    assert s["n"] == 3 and abs(s["median_xs_spy"] - 0.02) < 1e-12
    assert abs(s["win_rate"] - 2 / 3) < 1e-12


def test_bootstrap_is_reproducible_and_brackets_the_median():
    rng = np.random.default_rng(0)
    values = rng.normal(0.02, 0.05, 400)
    months = np.repeat(np.arange(40), 10)
    lo, hi = bootstrap_ci(values, months, np.median, 500, seed=7)
    assert lo < np.median(values) < hi
    assert (lo, hi) == bootstrap_ci(values, months, np.median, 500, seed=7)


def test_quintiles_rank_by_score():
    ev = _events(list(np.linspace(-0.1, 0.1, 50)))
    q = quintiles(ev, R)
    assert q["quintile"].tolist() == [1, 2, 3, 4, 5]
    assert q["median_xs_spy"].is_monotonic_increasing


def test_verdict_needs_every_test():
    good = {"n": 150, "median_xs_spy": 0.03, "win_rate": 0.6}
    group2 = {"median_xs_spy": 0.01}
    q = pd.DataFrame({"quintile": [1, 5], "n": [30, 30], "median_xs_spy": [0.0, 0.05]})
    assert verdict(good, group2, q, R)["passed"]
    v = verdict({**good, "n": 99}, group2, q, R)
    assert not v["passed"] and v["tests"] == {"n": False, "median_xs_spy": True, "win_rate": True,
                                               "beats_dislocated": True, "quintiles": True}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/research/test_stats.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `stats.py`**

`src/analyzer/research/stats.py`:

```python
"""Summary statistics, month-block bootstrap intervals (spec 9.5) and the go/no-go (spec 9.6)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

from analyzer.config import Rules


def is_win(events: pd.DataFrame, rules: Rules) -> pd.Series:
    h = rules.research.go_horizon
    col = f"xs_spy_{h}" if rules.research.win_basis == "excess_spy" else f"ret_{h}"
    return events[col] > 0


def bootstrap_ci(values: np.ndarray, months: np.ndarray, stat: Callable[[np.ndarray], float],
                 samples: int, seed: int) -> tuple[float, float]:
    if len(values) == 0:
        return (float("nan"), float("nan"))
    uniq = np.unique(months)
    groups = [values[months == m] for m in uniq]
    rng = np.random.default_rng(seed)
    stats = np.empty(samples)
    for i in range(samples):
        idx = rng.integers(0, len(uniq), len(uniq))
        stats[i] = stat(np.concatenate([groups[j] for j in idx]))
    lo, hi = np.nanpercentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def _months(events: pd.DataFrame) -> np.ndarray:
    return pd.DatetimeIndex(events["date"]).to_period("M").astype(str).to_numpy()


def summarize(events: pd.DataFrame, rules: Rules) -> dict:
    h = rules.research.go_horizon
    e = events[events[f"xs_spy_{h}"].notna()]
    r = rules.research
    xs = e[f"xs_spy_{h}"].to_numpy(dtype=float)
    wins = is_win(e, rules).to_numpy(dtype=float)
    months = _months(e)
    return {
        "n": int(len(e)),
        "median_ret": float(e[f"ret_{h}"].median()) if len(e) else float("nan"),
        "median_spy": float(e[f"spy_{h}"].median()) if len(e) else float("nan"),
        "median_xs_spy": float(np.median(xs)) if len(e) else float("nan"),
        "median_xs_ref": float(e[f"xs_ref_{h}"].median()) if len(e) else float("nan"),
        "win_rate": float(wins.mean()) if len(e) else float("nan"),
        "median_mae": float(e["mae"].median()) if len(e) else float("nan"),
        "ci_median_xs_spy": bootstrap_ci(xs, months, np.median, r.bootstrap_samples, r.bootstrap_seed),
        "ci_win_rate": bootstrap_ci(wins, months, np.mean, r.bootstrap_samples, r.bootstrap_seed),
    }


def quintiles(setups: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    h = rules.research.go_horizon
    e = setups[setups[f"xs_spy_{h}"].notna()]
    if len(e) < 5:
        return pd.DataFrame(columns=["quintile", "n", "median_xs_spy"])
    q = pd.qcut(e["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    g = e.groupby(q, observed=True)[f"xs_spy_{h}"]
    return pd.DataFrame({"quintile": [int(k) for k in g.groups], "n": g.size().to_numpy(),
                         "median_xs_spy": g.median().to_numpy()})


def breakdowns(events: pd.DataFrame, columns: Sequence[str], rules: Rules) -> pd.DataFrame:
    h = rules.research.go_horizon
    e = events[events[f"xs_spy_{h}"].notna()].assign(_win=lambda d: is_win(d, rules))
    parts = []
    for col in columns:
        if col not in e:
            continue
        g = e.groupby(e[col].astype(str))
        parts.append(pd.DataFrame({"column": col, "value": list(g.groups),
                                   "n": g.size().to_numpy(),
                                   "median_xs_spy": g[f"xs_spy_{h}"].median().to_numpy(),
                                   "win_rate": g["_win"].mean().to_numpy()}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["column", "value", "n", "median_xs_spy", "win_rate"])


def verdict(setups: dict, dislocated: dict, quintile_table: pd.DataFrame, rules: Rules) -> dict:
    r = rules.research
    q = quintile_table.set_index("quintile")["median_xs_spy"] if len(quintile_table) else pd.Series()
    tests = {
        "n": setups["n"] >= r.min_events,
        "median_xs_spy": bool(setups["median_xs_spy"] > 0),
        "win_rate": bool(setups["win_rate"] >= r.min_win_rate),
        "beats_dislocated": bool(setups["median_xs_spy"] > dislocated["median_xs_spy"]),
        "quintiles": bool(len(q) >= 2 and q.loc[q.index.max()] > q.loc[q.index.min()]),
    }
    return {"tests": tests, "passed": all(tests.values())}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/research/test_stats.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/research/stats.py tests/research/test_stats.py
git commit -m "feat: research statistics, month-block bootstrap and the go/no-go verdict"
```

---
### Task 24: Coverage report

**Files:**
- Create: `src/analyzer/research/coverage.py`
- Test: `tests/research/test_coverage.py`

**Interfaces:**
- Consumes: `Panels`, `Evaluation` (Task 20); `symbol_map` (Task 6); tables `index_membership`, `ticker_aliases`, `member_fates`.
- Produces: `coverage(conn, panels, evaluation) -> dict` with keys `members_by_year` (list of `{year, members, with_prices, share}`), `aliases` (`{source: count}`), `excluded` (`{fate: count}`), `identity_unverified` (int), `variants` (`{variant: symbols}`), `skipped_ticker_days` (`Evaluation.skipped`), `diluted_share_days`, `annual_ttm_days`, `fallback_days` (ints, over eligible ticker-days except the first, which is over member ticker-days).

- [ ] **Step 1: Write the failing test**

`tests/research/test_coverage.py`:

```python
import pandas as pd

from analyzer.research.coverage import coverage
from analyzer.signals.evaluate import evaluate, load_panels
from tests.fixture_db import build_fixture_db, fixture_rules


def test_coverage_on_the_fixture(tmp_path):
    conn = build_fixture_db(tmp_path / "a.db")
    conn.execute("INSERT INTO index_membership VALUES ('sp500', 'GONE', '2013-01-01', '2014-01-01')")
    conn.execute("INSERT INTO member_fates VALUES ('GONE', 'acquired', 'Acme acquired Gone')")
    rules = fixture_rules()
    panels = load_panels(conn, rules, end=pd.Timestamp("2016-12-30").date())
    c = coverage(conn, panels, evaluate(panels, rules))
    by_year = {r["year"]: r for r in c["members_by_year"]}
    assert by_year[2013]["members"] == 13 and by_year[2013]["with_prices"] == 12
    assert by_year[2016]["share"] == 1.0
    assert c["excluded"] == {"acquired": 1}
    assert c["variants"] == {"standard": 12}
    assert c["identity_unverified"] == 12            # the fixture has no snapshot CIKs
    assert c["skipped_ticker_days"]["history"] > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/research/test_coverage.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Write `coverage.py`**

`src/analyzer/research/coverage.py`:

```python
"""How much of the point-in-time universe research actually sees (spec 9.3, addendum 6.6)."""

from __future__ import annotations

import pandas as pd

from analyzer.panel.universe import symbol_map


def _members_by_year(conn, first_year: int, last_year: int) -> list[dict]:
    priced = symbol_map(conn)
    rows = conn.execute("SELECT ticker, start_date, COALESCE(end_date, '9999-12-31') "
                        "FROM index_membership").fetchall()
    out = []
    for year in range(first_year, last_year + 1):
        lo, hi = f"{year}-01-01", f"{year}-12-31"
        members = {t for t, s, e in rows if s <= hi and e > lo}
        with_prices = sum(1 for t in members if t in priced)
        out.append({"year": year, "members": len(members), "with_prices": with_prices,
                    "share": with_prices / len(members) if members else float("nan")})
    return out


def coverage(conn, panels, evaluation) -> dict:
    dates = panels.dates
    f = panels.fundamentals
    eligible = evaluation.eligible
    return {
        "members_by_year": _members_by_year(conn, dates[0].year, dates[-1].year),
        "aliases": dict(conn.execute(
            "SELECT source, COUNT(*) FROM ticker_aliases GROUP BY source").fetchall()),
        "excluded": dict(conn.execute(
            "SELECT fate, COUNT(*) FROM member_fates GROUP BY fate").fetchall()),
        "identity_unverified": int((~panels.entities["identity_verified"].astype(bool)).sum()),
        "variants": {k: int(v) for k, v in evaluation.variants.value_counts().items()},
        "skipped_ticker_days": evaluation.skipped,
        "diluted_share_days": int((evaluation.member & f["diluted_shares"]).to_numpy().sum()),
        "annual_ttm_days": int((eligible & f["annual"]).to_numpy().sum()),
        "fallback_days": int((eligible & f["fallback"]).to_numpy().sum()),
    }
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/research/test_coverage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/research/coverage.py tests/research/test_coverage.py
git commit -m "feat: research coverage report"
```

---

### Task 25: `run_research`, outputs, the OOS guard and log, and the CLI

**Files:**
- Create: `src/analyzer/research/run.py`
- Create: `docs/research/oos-runs.csv`
- Modify: `src/analyzer/cli.py` (add the `research` command group)
- Test: `tests/research/test_run.py`

**Interfaces:**
- Consumes: Tasks 20–24; `reasons` (Task 19); `PROJECT_ROOT`.
- Produces:
  - `PERIODS = {"in-sample": "in_sample", "oos": "oos"}`; `FROZEN_PATHS = ("rules.toml", "docs/specs/2026-10-24-phase-2-design-addendum.md")`; `OOS_LOG = PROJECT_ROOT / "docs/research/oos-runs.csv"`; `OOS_FIELDS`;
  - `git_state(root=PROJECT_ROOT, paths=FROZEN_PATHS) -> tuple[str, bool]` (HEAD commit, whether any frozen path has uncommitted changes);
  - `RunResult` (dataclass: `run_id: str`, `out_dir: Path`, `summary: dict`);
  - `run_research(conn, rules, period, out_root, *, panels=None, now=None) -> RunResult`; files written to `out_root/<run_id>/`: `events.csv`, `summary.json`, `breakdowns.csv`, `quintiles.csv`;
  - `append_oos_log(result, commit, path=OOS_LOG) -> None`;
  - CLI `analyzer research run [--period in-sample|oos] [--rules PATH]`.

- [ ] **Step 1: Write the failing tests**

`tests/research/test_run.py`:

```python
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
    assert setup_row["swing_outcome"] in {"target", "stop", "time", "open", "no_fill"}


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/research/test_run.py -v`
Expected: FAIL, `ImportError` on `analyzer.research.run`.

- [ ] **Step 3: Write `run.py`**

`src/analyzer/research/run.py`:

```python
"""`analyzer research run`: one research run, its files, and the out-of-sample log."""

from __future__ import annotations

import csv
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from analyzer.config import PROJECT_ROOT, Rules
from analyzer.research.coverage import coverage
from analyzer.research.events import (
    GROUP_DISLOCATED,
    GROUP_DISLOCATED_ALL,
    GROUP_SETUP,
    assign_period,
    select_events,
)
from analyzer.research.outcomes import forward_returns, simulate_plans
from analyzer.research.stats import breakdowns, quintiles, summarize, verdict
from analyzer.signals.evaluate import evaluate, load_panels
from analyzer.signals.reasons import reasons

log = logging.getLogger(__name__)

PERIODS = {"in-sample": "in_sample", "oos": "oos"}
FROZEN_PATHS = ("rules.toml", "docs/specs/2026-10-24-phase-2-design-addendum.md")
OOS_LOG = PROJECT_ROOT / "docs" / "research" / "oos-runs.csv"
OOS_FIELDS = ["run_date", "run_id", "rules_version", "commit", "n", "median_xs_spy", "win_rate",
              "dislocated_median_xs_spy", "verdict"]
BREAKDOWN_COLUMNS = ["shock", "correction", "sector", "industry", "regime", "variant", "tier",
                     "annual", "fallback", "has_missing"]


def git_state(root: Path = PROJECT_ROOT, paths=FROZEN_PATHS) -> tuple[str, bool]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                            text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=root,
                            capture_output=True, text=True, check=True).stdout.strip()
    return commit, bool(status)


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    summary: dict


def run_research(conn, rules: Rules, period: str, out_root: Path, *, panels=None,
                 now: pd.Timestamp | None = None) -> RunResult:
    key = PERIODS[period]
    now = now or pd.Timestamp.now()
    panels = panels or load_panels(conn, rules)
    log.info("evaluating %d dates", len(panels.dates))
    ev = evaluate(panels, rules)
    events = select_events(ev, rules.research.cooldown_days)
    events["period"] = assign_period(events, panels.dates, rules)
    ref_of = panels.entities["ref_etf"].to_dict()
    events = events.join(forward_returns(events, panels.stocks, panels.etfs, ref_of,
                                         rules.research.horizons, rules.research.go_horizon))
    is_setup = events["group"] == GROUP_SETUP
    events = events.join(simulate_plans(events[is_setup], panels.stocks, ev.trap_fails, rules.plans))
    events["reasons"] = ""
    events.loc[is_setup, "reasons"] = [" | ".join(reasons(r, rules))
                                       for r in events[is_setup].to_dict("records")]
    events["has_missing"] = events.get("missing", pd.Series(0, index=events.index)).fillna(0) > 0

    chosen = events[events["period"] == key]
    groups = {g: chosen[chosen["group"] == g] for g in (GROUP_SETUP, GROUP_DISLOCATED,
                                                         GROUP_DISLOCATED_ALL)}
    summaries = {g: summarize(e, rules) for g, e in groups.items()}
    q = quintiles(groups[GROUP_SETUP], rules)
    v = verdict(summaries[GROUP_SETUP], summaries[GROUP_DISLOCATED], q, rules)
    if key == "in_sample":
        v["note"] = "in-sample: for tuning only; the go/no-go is judged out of sample"

    run_id = f"{now:%Y%m%d-%H%M%S}-{key}"
    out_dir = Path(out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events.csv", index=False)
    pd.concat([breakdowns(e, BREAKDOWN_COLUMNS, rules).assign(group=g) for g, e in groups.items()],
              ignore_index=True).to_csv(out_dir / "breakdowns.csv", index=False)
    q.to_csv(out_dir / "quintiles.csv", index=False)
    summary = {
        "run_id": run_id, "period": key, "rules_version": rules.version,
        "guidance_tone_enabled": rules.gates.guidance_tone_enabled,
        "win_basis": rules.research.win_basis,
        "dates": [str(panels.dates[0].date()), str(panels.dates[-1].date())],
        "groups": summaries, "verdict": v,
        "plan_outcomes": {p: groups[GROUP_SETUP][f"{p}_outcome"].value_counts().to_dict()
                          for p in ("swing", "position") if f"{p}_outcome" in groups[GROUP_SETUP]},
        "coverage": coverage(conn, panels, ev),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=_json))
    return RunResult(run_id, out_dir, summary)


def _json(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return str(value)


def append_oos_log(result: RunResult, commit: str, path: Path = OOS_LOG) -> None:
    s = result.summary
    setup, dislocated = s["groups"][GROUP_SETUP], s["groups"][GROUP_DISLOCATED]
    row = {"run_date": result.run_id[:8], "run_id": result.run_id,
           "rules_version": s["rules_version"], "commit": commit, "n": setup["n"],
           "median_xs_spy": setup["median_xs_spy"], "win_rate": setup["win_rate"],
           "dislocated_median_xs_spy": dislocated["median_xs_spy"],
           "verdict": "pass" if s["verdict"]["passed"] else "fail"}
    new = not Path(path).exists() or Path(path).stat().st_size == 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OOS_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)
```

`docs/research/oos-runs.csv` (header only, committed):

```
run_date,run_id,rules_version,commit,n,median_xs_spy,win_rate,dislocated_median_xs_spy,verdict
```

- [ ] **Step 4: Add the CLI command**

In `src/analyzer/cli.py`, add `from pathlib import Path` to the imports, and append:

```python
research_app = typer.Typer(help="Research harness (spec 9).", no_args_is_help=True)
app.add_typer(research_app, name="research")


@research_app.command("run")
def research_run(
    period: str = typer.Option("in-sample", help="in-sample (2014-2021) or oos (2022 to today)."),
    rules_path: Path = typer.Option(None, "--rules", help="Another rules file (in-sample only)."),
) -> None:
    """Replay the strategy over history and write a report under data/research/."""
    from analyzer.config import PROJECT_ROOT
    from analyzer.research import run as research

    if period not in research.PERIODS:
        raise _fail(f"--period must be one of: {', '.join(research.PERIODS)}")
    if period == "oos" and rules_path is not None:
        raise _fail("the out-of-sample run uses rules.toml only (rules are fixed in advance)")
    commit = None
    if period == "oos":
        commit, dirty = research.git_state()
        if dirty:
            raise _fail("rules.toml or the phase-2 addendum has uncommitted changes; commit them "
                        "first so the out-of-sample run is reproducible")
    try:
        settings = load_settings()
        rules = load_rules(rules_path or PROJECT_ROOT / "rules.toml")
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"
        raise _fail(f"Configuration error: {reason}") from exc
    if not settings.db_path.exists():
        raise _fail("no database yet — run `analyzer update` first")
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        result = research.run_research(conn, rules, period, settings.data_dir / "research")
    finally:
        conn.close()
    s = result.summary
    for group, g in s["groups"].items():
        typer.echo(f"{group:15} n={g['n']:<6} median 63d excess vs SPY={g['median_xs_spy']:+.2%} "
                   f"win rate={g['win_rate']:.1%}")
    for name, ok in s["verdict"]["tests"].items():
        typer.echo(f"  {'PASS' if ok else 'FAIL'} {name}")
    typer.echo(f"verdict: {'PASS' if s['verdict']['passed'] else 'FAIL'}"
               f"{' (in-sample: tuning only)' if period == 'in-sample' else ''}")
    typer.echo(f"report: {result.out_dir}")
    if period == "oos":
        research.append_oos_log(result, commit)
        typer.echo(f"logged in {research.OOS_LOG}")
```

`_fail` returns a `typer.Exit(2)` after printing to stderr; `CliRunner` mixes stderr into `result.output` by default, which the tests read.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/research/test_run.py -v`
Expected: all PASS. NaN medians (the fixture's OOS run has no events) format as `+nan%` in the CLI, which is acceptable.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/research/run.py src/analyzer/cli.py docs/research/oos-runs.csv tests/research/test_run.py
git commit -m "feat: analyzer research run with outputs, OOS guard and OOS log"
```

---

### Task 26: Full test suite, lint and a timing check on the fixture

**Files:**
- Test: `tests/research/test_end_to_end.py`

**Interfaces:**
- Consumes: everything.
- Produces: one end-to-end test through the CLI on the fixture database.

- [ ] **Step 1: Write the end-to-end CLI test**

`tests/research/test_end_to_end.py`:

```python
import json
import time

from typer.testing import CliRunner

from analyzer.cli import app
from tests.fixture_db import build_fixture_db


def test_cli_in_sample_run_on_the_fixture(tmp_path, monkeypatch):
    data = tmp_path / "data"
    build_fixture_db(data / "analyzer.db").close()
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@test.invalid")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(data))
    rules = tmp_path / "rules.toml"
    from analyzer.config import PROJECT_ROOT
    rules.write_text((PROJECT_ROOT / "rules.toml").read_text().replace(
        'panel_start = "2009-01-01"', 'panel_start = "2012-01-01"'))
    started = time.monotonic()
    result = CliRunner().invoke(app, ["research", "run", "--rules", str(rules)])
    assert result.exit_code == 0, result.output
    assert "verdict: FAIL (in-sample: tuning only)" in result.output
    out = next((data / "research").iterdir())
    assert json.loads((out / "summary.json").read_text())["groups"]["setup"]["n"] >= 1
    assert time.monotonic() - started < 60
```

`load_settings()` reads `.env` from the project root; the environment variables set here override it, so the test never touches the owner's real database.

- [ ] **Step 2: Run the whole suite and lint**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: every test PASSES (445 from phase 1 plus the new ones) and ruff reports no errors. Fix any ruff finding in the file it names (unused imports are the likely ones).

- [ ] **Step 3: Commit**

```bash
git add tests/research/test_end_to_end.py
git commit -m "test: end-to-end research run through the CLI on the fixture database"
```

---

### Task 27: Live data refresh, first in-sample run and docs

This task uses the owner's real database and the network (Wikipedia, SEC, Yahoo). It does **not** run the out-of-sample period: the owner reviews the in-sample results and freezes the rules first (addendum section 9, step 3).

**Files:**
- Modify: `docs/specs/2026-10-24-phase-2-design-addendum.md` (section 3.3, last bullet)
- Modify: `docs/plans/2026-10-10-phase-1-status.md` (add a phase-2 section at the top)

- [ ] **Step 1: Refresh identity and fundamentals**

Run, one after the other:

```bash
uv run analyzer update --step universe     # snapshot CIKs, aliases, fates (mostly from cache)
uv run analyzer update --step edgar        # re-extracts all facts (new tag map) from cache, and
                                           # backfills companies whose CIK came from snapshots
uv run analyzer audit | head -30
```

Expected: the universe step's detail line shows `aliases=… recycled=… excluded=…`; the edgar step shows `reextracted=` about 1,780. Record the three numbers.

- [ ] **Step 2: Run the in-sample research and measure it**

```bash
/usr/bin/time -v uv run analyzer research run --period in-sample 2> data/research-time.txt
grep -E "Elapsed|Maximum resident" data/research-time.txt
```

Expected: a report folder under `data/research/`, the group lines, five test lines and `verdict: … (in-sample: tuning only)`. Target: under 10 minutes (addendum 6.7). Record the elapsed time and peak memory. If the run takes more than 10 minutes, profile with `uv run python -m cProfile -s cumtime -m analyzer.cli research run 2>&1 | head -40` and report the top entries to the owner instead of optimizing unasked.

- [ ] **Step 3: Sanity-check the report**

Open `summary.json` and check, then record in the status doc:
- `coverage.members_by_year`: share with prices per year (expect it to rise toward 1.0 in recent years);
- `coverage.excluded` and `coverage.aliases`;
- `groups.setup.n`, `groups.dislocated.n`, `groups.dislocated_all.n`;
- the five verdict tests.

Spot-check three setup events in `events.csv`: open the ticker's price chart around the date (any charting site) and confirm the drop, and confirm one fundamentals number against the 10-Q.

- [ ] **Step 4: Update the addendum and the status doc**

In the addendum, section 3.3, replace the last sentence of the "Stale share counts" bullet ("The audit reports how often this happens.") with: "The research coverage report counts how often this happens (`diluted_share_days`)."

At the top of `docs/plans/2026-10-10-phase-1-status.md`, add:

```markdown
## Phase 2 status (features, signals, research harness)

- Plan: `docs/plans/2026-10-24-phase-2-features-signals-research.md`; spec: the 2026-10-24 addendum.
- Built: panels, features, signals, `analyzer research run`. Guidance tone, watchlist, shorts,
  young listings and positions are not built yet (addendum 2.2).
- First in-sample run (<date>, rules <version>): <elapsed>, <peak memory>.
  - Coverage: <share with prices by year>; aliases <n>; excluded <by fate>.
  - Groups: setup n=<n>, dislocated n=<n>, dislocated_all n=<n>.
  - In-sample tests: <five lines>.
- Next: the owner reviews the in-sample results, may tune thresholds on in-sample data only,
  then freezes `rules.toml` (commit) and runs `analyzer research run --period oos` once.
```

Fill every `<…>` with the recorded values.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-10-24-phase-2-design-addendum.md docs/plans/2026-10-10-phase-1-status.md
git commit -m "docs: phase-2 first in-sample run and status"
```

- [ ] **Step 6: Report to the owner**

Summarize in plain words: what the in-sample run found, the coverage gaps, runtime and memory, and that the out-of-sample run is waiting for their review. Do not merge or push.
