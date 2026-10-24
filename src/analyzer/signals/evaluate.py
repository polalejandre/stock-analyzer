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
    ref_fallback: pd.DataFrame  # the day's reference ETF is a fallback (no window for the first)


def _long(mask: pd.DataFrame) -> pd.DataFrame:
    ri, ci = np.nonzero(mask.to_numpy(dtype=bool))
    rows = pd.DataFrame({"symbol": mask.columns[ci], "date": mask.index[ri], "pos": ri})
    return rows.sort_values(["date", "symbol"]).reset_index(drop=True)


def reference_chains(entities: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Each symbol's reference ETFs in order of preference: industry ETF, sector ETF (SPY is
    always last). The first with a 63-day return on a day is that day's reference (addendum 5.7)."""
    return {s: tuple(e for e in (r["industry_etf"], r["sector_etf"]) if isinstance(e, str) and e)
            for s, r in entities.iterrows()}


def _ref_names(choice: pd.DataFrame, etfs: pd.Index, rows: pd.DataFrame) -> np.ndarray:
    c = pick(choice, rows)
    names = np.asarray(list(etfs) + [None], dtype=object)
    return names[np.where(np.isnan(c) | (c < 0), len(etfs), np.nan_to_num(c)).astype(int)]


def evaluate(p: Panels, rules: Rules) -> Evaluation:
    f, feat = p.fundamentals, rules.features
    variants = variant_series(p.entities, f, rules.variants)
    ref_of = reference_chains(p.entities)
    # peers: p.member, the whole liquid index universe, not just names that pass eligibility (R24)
    vf = value_features(p.stocks.close, f, p.member, p.entities, columns_for_metrics(variants), feat)
    qf = quality_features(f, vf.net_debt, feat, rules.score)
    elig = eligibility(p.member, variants, f, vf.market_cap, rules)
    disl = dislocation(p.stocks, p.etfs.adj_close, p.etfs.close["SPY"], ref_of, feat, rules.gates)
    tech = technical(p.stocks, feat)
    # flow ranks industries over p.member: reference ETFs used by the universe (spec 5.7, R24)
    fl = flow(p.stocks, p.etfs, p.etfs.adj_close["SPY"], p.member, ref_of, disl.since_high, feat)
    trap = value_trap(qf, variants, rules)
    reg = regime(p.macro, rules.regime)

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
    rows["ref_etf"] = _ref_names(disl.ref_choice, p.etfs.adj_close.columns, rows)  # that day's
    rows["sector"] = rows["symbol"].map(p.entities["sector"])
    assert not rows["since_high"].isna().any(), "a dislocated row must have a 63-day high"
    start = (rows["pos"] - rows["since_high"]).astype(int)
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
    dislocated_all = _long(p.member & disl.dislocated)
    dislocated_all["ref_etf"] = _ref_names(disl.ref_choice, p.etfs.adj_close.columns, dislocated_all)
    first = pd.Series({s: (c[0] if c else "SPY") for s, c in ref_of.items()})
    first_pos = p.etfs.adj_close.columns.get_indexer(first.reindex(disl.ref_choice.columns)
                                                     .fillna("SPY"))
    ref_fallback = (disl.ref_choice >= 0) & (disl.ref_choice.to_numpy() != first_pos[None, :])
    return Evaluation(rows=rows, dislocated_all=dislocated_all,
                      member=p.member, eligible=elig.eligible, trap_fails=trap.fails,
                      skipped=elig.skipped, variants=variants, regime=reg,
                      ref_fallback=ref_fallback)
