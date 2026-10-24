import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.flow import COLD, HOT, NEUTRAL, active_etfs, etf_flow_codes, flow
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
