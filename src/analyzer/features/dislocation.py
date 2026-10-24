"""Dislocation (spec 5.3) and gate 2 (spec 7.1)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    ref_choice: pd.DataFrame  # per day, position in the ETF columns of the reference used (-1 none)


RefOf = Mapping[str, "str | Sequence[str]"]


def _chain(ref, etfs: pd.Index) -> tuple[int, ...]:
    """Positions in `etfs` of a stock's reference ETFs in order of preference, SPY last."""
    names = [ref] if isinstance(ref, str) else [r for r in (ref or ()) if isinstance(r, str)]
    out: list[int] = []
    for name in [*names, "SPY"]:
        j = etfs.get_loc(name) if name in etfs else -1
        if j >= 0 and j not in out:
            out.append(j)
    return tuple(out)


def reference_choice(etf_adj: pd.DataFrame, ref_of: RefOf, columns, days: int) -> pd.DataFrame:
    """Per day and stock, the position in `etf_adj.columns` of the reference ETF used: the first of
    the stock's chain (industry ETF, sector ETF, SPY; a plain name means that ETF, then SPY) that
    has a `days`-day return that day; -1 when none has. An ETF listed after the stock's history
    starts (XLRE 2015, XLC 2018) therefore takes over only once it has its own window."""
    has = (etf_adj / etf_adj.shift(days) - 1).notna().to_numpy()
    out = np.full((len(etf_adj.index), len(columns)), -1, dtype=np.int16)
    by_chain: dict[tuple[int, ...], list[int]] = {}
    for i, c in enumerate(columns):
        by_chain.setdefault(_chain(ref_of.get(c, "SPY"), etf_adj.columns), []).append(i)
    for chain, cols in by_chain.items():
        pick = np.full(len(etf_adj.index), -1, dtype=np.int16)
        for j in reversed(chain):
            pick = np.where(has[:, j], j, pick)
        out[:, cols] = pick[:, None]
    return pd.DataFrame(out, index=etf_adj.index, columns=list(columns))


def take(frame: pd.DataFrame, choice: pd.DataFrame) -> pd.DataFrame:
    """Per day and stock, the value of `frame` (ETF-shaped) in the column `choice` points to."""
    arr = frame.reindex(index=choice.index).to_numpy(dtype="float64")
    c = choice.to_numpy()
    vals = np.take_along_axis(arr, np.maximum(c, 0).astype(np.intp), axis=1)
    return pd.DataFrame(np.where(c >= 0, vals, np.nan), index=choice.index, columns=choice.columns)


def reference_returns(etf_adj: pd.DataFrame, ref_of: RefOf, columns, days: int,
                      choice: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each stock's reference-ETF return over `days`, as a stock-shaped frame (per-day fallback
    along the stock's chain, see `reference_choice`)."""
    if choice is None:
        choice = reference_choice(etf_adj, ref_of, columns, days)
    return take(etf_adj / etf_adj.shift(days) - 1, choice)


def dislocation(prices: PricePanels, etf_adj: pd.DataFrame, spy_close: pd.Series,
                ref_of: RefOf, features: FeatureRules, gates: GateRules) -> Dislocation:
    w = features.high_window
    close = prices.close
    high = close.rolling(w, min_periods=w).max()
    since = pd.DataFrame(since_max(close.to_numpy(dtype="float64"), w),
                         index=close.index, columns=close.columns)
    drawdown = 1 - close / high
    stock_ret = prices.adj_close / prices.adj_close.shift(w) - 1
    choice = reference_choice(etf_adj.reindex(index=close.index), ref_of, close.columns, w)
    gap = stock_ret - reference_returns(etf_adj.reindex(index=close.index), ref_of, close.columns,
                                        w, choice)

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
                       window_low=window_low, dislocated=dislocated.astype(bool),
                       ref_choice=choice)
