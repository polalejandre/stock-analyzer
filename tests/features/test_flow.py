import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.flow import (
    COLD,
    HOT,
    NEUTRAL,
    active_etfs,
    etf_flow_codes,
    flow,
    stock_codes,
)
from analyzer.panel.prices import PricePanels
from tests.panel_fixtures import assert_truncation_invariant, cut, make_price_panels, random_walk

F = load_rules().features


def _etfs(n=200):
    idx = pd.bdate_range("2022-01-03", periods=n)

    def grow(r):
        return 100 * (1 + r) ** np.arange(n)

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
    assert last["E1"] == NEUTRAL                          # second of five: not in the top 20%
    assert (codes.iloc[-1] == HOT).sum() == 1 and (codes.iloc[-1] == COLD).sum() == 1


def test_rising_volume_does_not_make_second_place_hot():
    closes = _etfs()
    p = make_price_panels(closes, volume=1e6)
    vol = p.volume.copy()
    vol.iloc[-20:, 1] = 3e6
    p = PricePanels(p.open, p.high, p.low, p.close, p.adj_close, vol)
    active = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    codes = etf_flow_codes(p, pd.Series(100.0, index=closes.index), active, F)
    assert codes.iloc[-1]["E1"] == NEUTRAL


def test_too_few_industries_is_unknown():
    closes = _etfs()
    active = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    active["E0"] = False
    codes = etf_flow_codes(make_price_panels(closes), pd.Series(100.0, index=closes.index), active, F)
    assert codes.iloc[-1].isna().all()


def test_ten_etfs_two_hot_two_cold():
    n = 200
    idx = pd.bdate_range("2022-01-03", periods=n)
    rates = np.linspace(0.005, -0.004, 10)
    closes = pd.DataFrame({f"E{i}": 100 * (1 + r) ** np.arange(n) for i, r in enumerate(rates)}, index=idx)
    p = make_price_panels(closes, volume=1e6)
    vol = p.volume.copy()
    vol.iloc[-20:, :2] = 3e6
    p = PricePanels(p.open, p.high, p.low, p.close, p.adj_close, vol)
    active = pd.DataFrame(True, index=idx, columns=closes.columns)
    last = etf_flow_codes(p, pd.Series(100.0, index=idx), active, F).iloc[-1]
    assert (last == HOT).sum() == 2 and (last == COLD).sum() == 2
    assert last["E0"] == HOT and last["E1"] == HOT and last["E8"] == COLD and last["E9"] == COLD


def test_inactive_etfs_are_not_ranked():
    closes = _etfs()
    active = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    active["E0"] = False
    closes["E5"] = closes["E0"]
    active["E5"] = True
    codes = etf_flow_codes(make_price_panels(closes), pd.Series(100.0, index=closes.index), active, F)
    assert np.isnan(codes.iloc[-1]["E0"]) and not np.isnan(codes.iloc[-1]["E5"])


def test_active_etfs():
    idx = pd.bdate_range("2024-01-01", periods=2)
    eligible = pd.DataFrame({"A": [True, False], "B": [False, False]}, index=idx)
    choice = pd.DataFrame({"A": [0, 0], "B": [1, 1]}, index=idx)   # positions in the ETF list
    a = active_etfs(eligible, choice, ["XLK", "XLE"])
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


def _window(closes, vols):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    c = pd.DataFrame({"A": closes}, index=idx)
    v = pd.DataFrame({"A": vols}, index=idx)
    p = PricePanels(c, c, c, c, c, v)
    etf = make_price_panels(random_walk(["XLK"], n=len(closes), seed=1, start="2024-01-01"))
    spy = pd.Series(100.0, index=idx)
    elig = pd.DataFrame(True, index=idx, columns=["A"])
    since = pd.DataFrame(10.0, index=idx, columns=["A"])
    return flow(p, etf, spy, elig, {"A": "XLK"}, since, F)


def test_updown_ratio_and_accumulation_values():
    # moves after the first bar: up, down, up, down, up  -> up avg 200, down avg 100
    r = _window([10, 11, 10, 11, 10, 11], [1, 100, 100, 300, 100, 200])
    last = r.updown_ratio["A"].iloc[-1]
    assert abs(last - 2.0) < 1e-12                       # up avg 200 / down avg 100
    assert not r.obv_divergence["A"].iloc[-1]
    assert abs(r.accumulation["A"].iloc[-1] - 0.5) < 1e-12   # ratio 2 clips to 1, no divergence


def test_nan_ratio_scores_half():
    r = _window([10, 11, 12, 13, 14, 15], [1, 100, 100, 100, 100, 100])   # no down days
    assert np.isinf(r.updown_ratio["A"].iloc[-1]) or np.isnan(r.updown_ratio["A"].iloc[-1])
    flat = _window([10.0] * 6, [100] * 6)                                  # no moves at all
    assert np.isnan(flat.updown_ratio["A"].iloc[-1])
    assert flat.accumulation["A"].iloc[-1] == 0.25


def test_obv_divergence_detected():
    # first low 10 after heavy selling; second, lower low 9.5 on light selling: OBV higher
    close = [20, 18, 16, 14, 12, 10, 12, 14, 16, 18, 20, 18, 16, 14, 12, 9.5,
             12, 14, 16, 18, 20, 22, 24]
    vol = [1000] * 6 + [100] * 5 + [50] * 5 + [100] * 7
    r = _window(close, vol)
    assert r.obv_divergence["A"].iloc[-1]
    assert r.accumulation["A"].iloc[-1] >= 0.5


def test_stock_codes_follow_the_days_reference_etf():
    idx = pd.bdate_range("2024-01-01", periods=3)
    codes = pd.DataFrame({"XLRE": [np.nan, np.nan, HOT], "XLF": [COLD, COLD, COLD]}, index=idx)
    choice = pd.DataFrame({"A": [-1, 1, 0]}, index=idx)          # none, then XLF, then XLRE
    out = stock_codes(codes, choice)["A"]
    assert np.isnan(out.iloc[0]) and out.iloc[1] == COLD and out.iloc[2] == HOT


def test_flow_tag_falls_back_with_the_reference_etf():
    names = ["A", "B", "C", "D", "E"]
    stocks = make_price_panels(random_walk(names, n=300, seed=31))
    etf = random_walk(["XLRE", "XLF", "XLK", "XLE", "XLV", "XLI"], n=300, seed=32)
    etf.iloc[:200, 0] = np.nan                                   # XLRE lists on day 200
    etf = make_price_panels(etf)
    spy = random_walk(["SPY"], n=300, seed=33)["SPY"]
    eligible = pd.DataFrame(True, index=stocks.dates, columns=names)
    since = pd.DataFrame(10.0, index=stocks.dates, columns=names)
    others = {"B": "XLK", "C": "XLE", "D": "XLV", "E": "XLI"}
    r = flow(stocks, etf, spy, eligible, {"A": ("XLRE", "XLF"), **others}, since, F)
    ref = flow(stocks, etf, spy, eligible, {"A": "XLF", **others}, since, F)
    # until XLRE has a 63-day window, A's tag is XLF's, ranked among the five ETFs in use
    before = r.industry["A"].iloc[:263]
    assert before.notna().sum() > 50
    pd.testing.assert_series_equal(before, ref.industry["A"].iloc[:263])
    assert r.industry["A"].iloc[263:].isna().all()               # XLRE: no 126-day RS yet
