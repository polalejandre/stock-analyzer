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
