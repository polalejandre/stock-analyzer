"""Flow (spec 5.4): money going into the stock's industry, and accumulation during the dip."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.features.dislocation import RefOf, reference_choice, take
from analyzer.features.technical import bullish_divergence, confirmed_lows, pivot_pairs
from analyzer.features.windows import reduce_since
from analyzer.panel.prices import PricePanels

HOT, NEUTRAL, COLD = 1.0, 0.0, -1.0


def active_etfs(eligible: pd.DataFrame, choice: pd.DataFrame, etfs: Sequence[str]) -> pd.DataFrame:
    """Per day, the ETFs that are the reference of at least one eligible stock that day
    (`choice`: positions in `etfs`, from `reference_choice`)."""
    e = eligible.reindex(index=choice.index, columns=choice.columns, fill_value=False)
    e = e.to_numpy(dtype=bool)
    c = choice.to_numpy()
    out = {etf: (e & (c == j)).any(axis=1) for j, etf in enumerate(etfs)}
    return pd.DataFrame(out, index=choice.index, columns=list(etfs))


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
    ranked = rs.where(mask)
    n = ranked.notna().sum(axis=1)
    top = ranked.rank(axis=1, ascending=False, method="min")
    bottom = ranked.rank(axis=1, ascending=True, method="min")
    eps = 1e-9                                   # rank / n <= q, free of float noise
    hot = top.le(n * (1 - rules.hot_rank) + eps, axis=0) & (trend > rules.hot_dv_trend)
    cold = bottom.le(n * rules.cold_rank + eps, axis=0)
    codes = pd.DataFrame(np.where(hot, HOT, np.where(cold, COLD, NEUTRAL)),
                         index=rs.index, columns=rs.columns)
    enough = (n >= rules.flow_min_industries).to_numpy()[:, None]
    return codes.where(ranked.notna() & enough)


def stock_codes(codes: pd.DataFrame, choice: pd.DataFrame) -> pd.DataFrame:
    """Each stock's flow code: the code of its reference ETF that day (`choice`, positions in
    `codes.columns`), with the same per-day fallback as the dislocation gap."""
    return take(codes, choice)


@dataclass(frozen=True)
class Flow:
    industry: pd.DataFrame
    updown_ratio: pd.DataFrame
    obv_divergence: pd.DataFrame
    accumulation: pd.DataFrame


def flow(prices: PricePanels, etf: PricePanels, spy_adj: pd.Series, eligible: pd.DataFrame,
         ref_of: RefOf, since_high: pd.DataFrame, rules: FeatureRules) -> Flow:
    close, volume = prices.close, prices.volume
    etf_adj = etf.adj_close.reindex(index=close.index)
    choice = reference_choice(etf_adj, ref_of, close.columns, rules.high_window)
    active = active_etfs(eligible, choice, list(etf.close.columns))
    industry = stock_codes(etf_flow_codes(etf, spy_adj, active, rules).reindex(index=close.index),
                           choice)

    change = prices.adj_close.diff()
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
    ratio_score = ((ratio - rules.accumulation_low) / span).clip(0, 1).fillna(0.5)
    accumulation = 0.5 * ratio_score + 0.5 * obv_div.astype(float)
    return Flow(industry=industry, updown_ratio=ratio, obv_divergence=obv_div,
                accumulation=accumulation)
