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
    closes = random_walk(["A", "B"], n=600, seed=1)
    full = weekly_divergence(closes, closes, F)
    wednesdays = [d for d in closes.index[300:] if d.dayofweek == 2][:3]
    for d in wednesdays:                       # the test must exercise real divergences
        assert full.loc[d - pd.Timedelta(days=21):d].any().any()
    assert_truncation_invariant(
        lambda end: weekly_divergence(cut(closes, end), cut(closes, end), F), wednesdays)


def test_partial_final_week_does_not_complete_a_divergence():
    closes = random_walk(["A"], n=300, seed=1)
    monday = pd.Timestamp("2018-06-11")
    wednesday = pd.Timestamp("2018-06-06")
    friday = pd.Timestamp("2018-06-08")
    assert monday in closes.index and wednesday in closes.index
    div = lambda end: weekly_divergence(cut(closes, end), cut(closes, end), F)["A"]  # noqa: E731
    assert not div(wednesday).iloc[-1]
    assert not div(friday).iloc[-1]
    assert div(monday).iloc[-1]                # the finished week is usable from the next Monday


def test_quick_reclaim_after_break():
    low = pd.DataFrame({"A": [20.0, 18, 15, 12, 11, 10, 13, 16, 17, 16, 15, 14, 13, 12, 9,
                              11, 12, 13, 14]})
    k = 2
    pairs = pivot_pairs(low, low, confirmed_lows(low, k), k)
    rec = reclaimed_support(low, pairs, bars=10)
    assert not rec["A"].iloc[14]
    assert rec["A"].iloc[15]                   # first close back above 10 after the break at 14


def test_reclaim_after_window_and_no_break():
    low = pd.DataFrame({"A": [20.0, 18, 15, 12, 11, 10, 13, 16, 17, 16, 15, 14, 13, 12, 9]
                        + [9.0] * 12 + [11.0, 12.0]})
    k = 2
    pairs = pivot_pairs(low, low, confirmed_lows(low, k), k)
    rec = reclaimed_support(low, pairs, bars=10)
    assert not rec["A"].iloc[-2:].any()        # break at 14, reclaim at 27: more than 10 bars
    flat = pd.DataFrame({"A": [20.0, 18, 15, 12, 11, 10, 13, 16, 17, 18, 19, 20, 21, 22]})
    pairs = pivot_pairs(flat, flat, confirmed_lows(flat, k), k)
    assert not reclaimed_support(flat, pairs, bars=10)["A"].any()


def test_double_bottom_gives_one_pivot():
    low = pd.DataFrame({"A": [5.0, 4, 3, 2, 2, 3, 4, 5, 6]})
    assert confirmed_lows(low, 2)["A"].sum() == 1


def test_rsi_flat_is_fifty_and_nan_after_last_price():
    flat = pd.DataFrame({"A": [10.0] * 30})
    assert rsi(flat, 14)["A"].iloc[-1] == 50.0
    c = pd.DataFrame({"A": list(np.arange(1.0, 31.0)) + [np.nan] * 3})
    assert rsi(c, 14)["A"].iloc[-3:].isna().all()
    assert atr(c + 1, c - 1, c, 14)["A"].iloc[-3:].isna().all()


def test_daily_features_are_truncation_invariant():
    from analyzer.features.technical import technical
    from tests.panel_fixtures import make_price_panels
    p = make_price_panels(random_walk(["A", "B"], n=300, seed=12))
    def t(end):
        r = technical(cut(p, end), F)
        return {"rsi": r.rsi, "atr": r.atr, "ma": r.ma, "div": r.divergence, "rec": r.reclaimed}

    assert_truncation_invariant(t, [p.dates[120], p.dates[250]])
