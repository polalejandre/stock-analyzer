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
    def t(end):
        r = technical(cut(p, end), F)
        return {"rsi": r.rsi, "atr": r.atr, "div": r.divergence, "rec": r.reclaimed}

    assert_truncation_invariant(t, [p.dates[120], p.dates[250]])
