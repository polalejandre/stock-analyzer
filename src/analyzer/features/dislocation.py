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
