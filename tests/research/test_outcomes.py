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


def _custom(close, open_=None, low=None):
    idx = pd.bdate_range("2024-01-01", periods=len(close))
    c = pd.DataFrame({"A": close}, index=idx)
    o = c if open_ is None else pd.DataFrame({"A": open_}, index=idx)
    lo = c if low is None else pd.DataFrame({"A": low}, index=idx)
    return PricePanels(open=o, high=c, low=lo, close=c, adj_close=c,
                       volume=pd.DataFrame(1e6, index=idx, columns=c.columns))


def _etfs(n, spy, xlk):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return make_price_panels(pd.DataFrame({"SPY": spy, "XLK": xlk}, index=idx))


def test_entry_is_open_mae_is_exact_and_ref_differs_from_spy():
    close = np.array([10.0, 11, 12, 13, 14, 15])
    s = _custom(close, open_=close - 0.5, low=close - 1)
    spy = np.array([100.0, 100, 101, 102, 103, 104])
    xlk = np.array([50.0, 50, 55, 56, 57, 58])
    e = _etfs(6, spy, xlk)
    ev = pd.DataFrame({"symbol": ["A"], "pos": [0]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=3).iloc[0]
    assert out["entry_price"] == 10.5
    assert abs(out["ret_2"] - (12 / 10.5 - 1)) < 1e-12
    assert abs(out["mae"] - (10 / 10.5 - 1)) < 1e-12
    assert abs(out["xs_spy_2"] - (12 / 10.5 - 1 - 0.01)) < 1e-12
    assert abs(out["xs_ref_2"] - (12 / 10.5 - 1 - 0.1)) < 1e-12
    assert out["xs_ref_2"] != out["xs_spy_2"]
    assert not out["delisted_2"]


def test_delisted_stock_exits_at_last_close_and_benchmark_runs_on():
    n = 80
    close = 10 + 0.1 * np.arange(n)
    close[41:] = np.nan
    s = _custom(close)
    spy = 100.0 + np.arange(n)
    e = _etfs(n, spy, spy)
    ev = pd.DataFrame({"symbol": ["A"], "pos": [5]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(63,), mae_horizon=10).iloc[0]
    assert abs(out["ret_63"] - (close[40] / close[6] - 1)) < 1e-12
    assert out["delisted_63"]
    assert abs(out["spy_63"] - (spy[68] / spy[6] - 1)) < 1e-12


def test_gap_at_exit_uses_last_valid_close_and_is_not_delisted():
    n = 40
    close = 10 + 0.1 * np.arange(n)
    close[20] = np.nan
    s = _custom(close)
    e = _etfs(n, np.full(n, 100.0), np.full(n, 100.0))
    ev = pd.DataFrame({"symbol": ["A"], "pos": [10]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(10,), mae_horizon=5).iloc[0]
    assert abs(out["ret_10"] - (close[19] / close[11] - 1)) < 1e-12
    assert not out["delisted_10"]


def test_data_ending_before_the_horizon_is_nan_for_everything():
    n = 30
    s = _custom(np.full(n, 10.0))
    e = _etfs(n, np.full(n, 100.0), np.full(n, 100.0))
    ev = pd.DataFrame({"symbol": ["A"], "pos": [5]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(63,), mae_horizon=10).iloc[0]
    assert np.isnan(out["ret_63"]) and np.isnan(out["spy_63"]) and not out["delisted_63"]


def test_thesis_on_the_time_stop_day_is_a_time_exit():
    o = c = np.full(30, 10.0)
    plan = _plan(position_stop=7.0, position_target=20.0)
    trap = np.zeros(30, bool)
    trap[20] = True        # entry day 1, time stop 20 -> last holding day is 20
    assert simulate_plan(o, c, trap, 0, plan, "position", 20)[::3] == ("time", 20)
    trap = np.zeros(30, bool)
    trap[19] = True
    assert simulate_plan(o, c, trap, 0, plan, "position", 20)[0] == "thesis"


def test_plan_on_a_series_that_ends_is_delisted():
    o = c = np.array([10.0, 10, 11, np.nan, np.nan, np.nan])
    outcome, price, r, days = simulate_plan(o, c, np.zeros(6, bool), 0, _plan(), "swing", 20)
    assert (outcome, price, days) == ("delisted", 11.0, 2) and abs(r - 0.5) < 1e-12


def test_xs_ref_uses_the_events_own_reference_etf():
    idx = pd.bdate_range("2024-01-01", periods=6)
    s = make_price_panels(pd.DataFrame({"A": [10.0, 11, 12, 13, 14, 15]}, index=idx))
    e = make_price_panels(pd.DataFrame({"SPY": [100.0] * 6, "XLK": [100.0, 100, 110, 110, 110, 110],
                                        "XLF": [100.0, 100, 100, 100, 100, 100]}, index=idx))
    ev = pd.DataFrame({"symbol": ["A", "A"], "pos": [0, 0], "ref_etf": ["XLF", "XLK"]})
    out = forward_returns(ev, s, e, {"A": "XLK"}, horizons=(2,), mae_horizon=2)
    assert abs(out.loc[0, "xs_ref_2"] - (12 / 11 - 1)) < 1e-12           # XLF flat
    assert abs(out.loc[1, "xs_ref_2"] - (12 / 11 - 1 - 0.1)) < 1e-12     # XLK +10%
