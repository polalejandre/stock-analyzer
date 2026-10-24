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
    out = out.where(loss > 0, 100.0).where((gain > 0) | (loss > 0), 50.0)
    return out.where(gain.notna() & close.notna())


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int) -> pd.DataFrame:
    prev = close.shift(1)
    tr = np.fmax(np.fmax(high - low, (high - prev).abs()), (low - prev).abs())
    out = tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return out.where(close.notna())


def confirmed_lows(low: pd.DataFrame, k: int) -> pd.DataFrame:
    """Swing low: strictly below the k bars before, at or below the k bars after (so a double
    bottom gives one pivot, the first). Known k bars later."""
    left = low.rolling(k, min_periods=k).min().shift(1)
    right = low.rolling(k, min_periods=k).min().shift(-k)
    is_low = (low < left) & (low <= right)
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
    """A close broke below the latest confirmed swing low (known that day), and within the next
    `bars` days a close is back above that same level."""
    level = pairs.last_price
    broke = close < level
    out = pd.DataFrame(False, index=close.index, columns=close.columns)
    for d in range(1, bars + 1):
        out |= broke.shift(d, fill_value=False) & (close > level.shift(d))
    return out.astype(bool)


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
