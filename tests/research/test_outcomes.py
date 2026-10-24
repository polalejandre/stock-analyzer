import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.panel.prices import PricePanels
from analyzer.research.outcomes import forward_returns, simulate_plan, simulate_plans
from tests.panel_fixtures import make_price_panels

P = load_rules().plans


def _panels(stock, spy):
    idx = pd.bdate_range("2024-01-01", periods=len(stock))
    s = make_price_panels(pd.DataFrame({"A": stock}, index=idx))
    e = make_price_panels(pd.DataFrame({"SPY": spy, "XLK": spy}, index=idx))
    return s, e


def test_returns_start_at_the_next_open():
    s, e = _panels([10.0, 11, 12, 13, 14, 15], [100.0, 100, 101, 102, 103, 104])
    ev = pd.DataFrame({"symbol": ["A"], "pos": [0]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=3).iloc[0]
    assert out["entry_price"] == 11.0
    assert abs(out["ret_2"] - (12 / 11 - 1)) < 1e-12
    assert abs(out["xs_spy_2"] - ((12 / 11 - 1) - (101 / 100 - 1))) < 1e-12
    assert out["mae"] == 0.0


def test_event_on_last_day_has_no_outcome():
    s, e = _panels([10.0, 11, 12], [100.0, 100, 100])
    ev = pd.DataFrame({"symbol": ["A", "A"], "pos": [2, 1]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=2)
    assert np.isnan(out.loc[0, "entry_price"]) and np.isnan(out.loc[0, "ret_2"])
    assert out.loc[1, "entry_price"] == 12.0 and np.isnan(out.loc[1, "ret_2"])   # window incomplete


def test_forward_returns_use_adjusted_prices():
    idx = pd.bdate_range("2024-01-01", periods=6)
    raw = pd.DataFrame({"A": [10.0, 11, 12, 13, 14, 15]}, index=idx)
    adj = raw.copy()
    adj.iloc[:3] = adj.iloc[:3] * 0.9          # a dividend after day 2 lowers earlier adjusted prices
    s = PricePanels(open=raw, high=raw, low=raw, close=raw, adj_close=adj,
                    volume=pd.DataFrame(1e6, index=idx, columns=raw.columns))
    _, e = _panels([10.0] * 6, [100.0] * 6)
    ev = pd.DataFrame({"symbol": ["A"], "pos": [0]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(3,), mae_horizon=3).iloc[0]
    assert abs(out["entry_price"] - 11 * 0.9) < 1e-12
    assert abs(out["ret_3"] - (13 / (11 * 0.9) - 1)) < 1e-12
    assert abs(out["ret_3"] - (13 / 11 - 1)) > 0.1


def _plan(**over):
    base = dict(zone_low=9.0, zone_high=11.0, swing_stop=8.0, swing_target=13.0)
    base.update(over)
    return base


def test_plan_hits_target():
    o = c = np.array([10.0, 10, 11, 13.5, 9])
    assert simulate_plan(o, c, np.zeros(5, bool), 0, _plan(), "swing", 20)[:2] == ("target", 13.5)


def test_plan_hits_stop_and_reports_r():
    o = c = np.array([10.0, 10, 7.5])
    outcome, price, r, days = simulate_plan(o, c, np.zeros(3, bool), 0, _plan(), "swing", 20)
    assert outcome == "stop" and price == 7.5 and abs(r - (-2.5 / 2)) < 1e-12 and days == 2


def test_gap_outside_zone_is_no_fill():
    o = np.array([10.0, 12.0, 12.0])
    assert simulate_plan(o, o, np.zeros(3, bool), 0, _plan(), "swing", 20)[0] == "no_fill"


def test_time_stop_and_open_trade():
    o = c = np.full(30, 10.0)
    assert simulate_plan(o, c, np.zeros(30, bool), 0, _plan(), "swing", 20)[0] == "time"
    assert simulate_plan(o[:10], c[:10], np.zeros(10, bool), 0, _plan(), "swing", 20)[0] == "open"


def test_thesis_stop_exits_next_open_for_position_plans_only():
    o = c = np.array([10.0, 10, 10, 10.5, 10])
    trap = np.array([False, False, True, True, True])
    plan = _plan(position_stop=7.0, position_target=20.0)
    assert simulate_plan(o, c, trap, 0, plan, "position", 126)[:2] == ("thesis", 10.5)
    assert simulate_plan(o, c, trap, 0, plan, "swing", 20)[0] == "open"


def test_simulate_plans_two_symbols_with_trap_frame():
    idx = pd.bdate_range("2024-01-01", periods=30)
    a = np.full(30, 13.5)
    a[:3] = [10.0, 10.0, 11.0]
    closes = pd.DataFrame({"A": a, "B": np.full(30, 10.0)}, index=idx)
    stocks = make_price_panels(closes)
    trap = pd.DataFrame(False, index=idx, columns=["A", "B"])
    trap.loc[idx[2], "A"] = True
    base = dict(pos=0, zone_low=9.0, zone_high=11.0, swing_stop=8.0, position_stop=7.0)
    events = pd.DataFrame([
        dict(symbol="A", swing_target=13.0, position_target=20.0, **base),
        dict(symbol="B", swing_target=13.0, position_target=np.nan, **base),
    ], index=[5, 9])
    out = simulate_plans(events, stocks, trap, P)
    assert list(out.index) == [5, 9]
    a_row, b_row = out.loc[5], out.loc[9]
    assert a_row["swing_outcome"] == "target" and a_row["swing_days"] == 3
    assert abs(a_row["swing_r"] - (13.5 - 10) / 2) < 1e-12
    assert a_row["position_outcome"] == "thesis" and a_row["position_days"] == 3
    assert abs(a_row["position_r"] - (13.5 - 10) / 3) < 1e-12
    assert b_row["swing_outcome"] == "time" and b_row["swing_days"] == P.swing_time_stop
    assert b_row["position_outcome"] == "no_plan" and b_row["position_days"] == 0
    assert np.isnan(b_row["position_r"])
