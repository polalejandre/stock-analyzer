import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.value import ValueFeatures
from analyzer.signals.plans import make_plans, valuation_target

R = load_rules()


def _rows(**over):
    base = dict(close=80.0, atr=2.0, window_low=78.0, high=100.0, ma=95.0, val_target=110.0)
    base.update(over)
    return pd.DataFrame([base])


def test_swing_plan_levels_and_reward_risk():
    p = make_plans(_rows(), R.plans, R.gates).iloc[0]
    assert p["entry"] == 80 and p["zone_low"] == 79 and p["zone_high"] == 81
    assert p["swing_stop"] == 78 - 4
    assert p["swing_target"] == 89                  # 78 + 0.5 * (100 - 78), below the MA of 95
    assert abs(p["swing_rr"] - 9 / 6) < 1e-9 and p["swing_ok"]
    assert p["position_stop"] == 78 - 6
    assert p["position_target"] == 100              # high (100) is closer than the value target
    assert p["gate5"]


def test_targets_below_entry_do_not_count():
    # retrace = 98 + 0.5 * (100 - 98) = 99 and the MA (90) are not above the entry of 99
    p = make_plans(_rows(close=99.0, ma=90.0, window_low=98.0, val_target=95.0), R.plans, R.gates).iloc[0]
    assert np.isnan(p["swing_target"]) and not p["swing_ok"]
    assert p["position_target"] == 100                # the value target (95) is below entry
    assert not p["gate5"]                             # R/R 1 / 7


def test_poor_reward_risk_fails_gate5():
    p = make_plans(_rows(close=80.0, atr=8.0, window_low=78.0, high=84.0, ma=83.0, val_target=np.nan),
                   R.plans, R.gates).iloc[0]
    assert not p["swing_ok"] and not p["position_ok"] and not p["gate5"]


def test_valuation_target_per_metric_and_median():
    d = pd.Timestamp("2024-06-03")
    syms = ["A", "B", "C"]

    def frame(cols, vals):
        return pd.DataFrame([vals], index=[d], columns=cols, dtype="float64")

    vf = ValueFeatures(
        ratio={"pe": frame(["A", "C"], [20.0, np.nan]), "ev_ebitda": frame(["B", "C"], [10.0, np.nan])},
        pct={}, peer={},
        p50={"pe": frame(["A", "C"], [15.0, np.nan]), "ev_ebitda": frame(["B", "C"], [8.0, np.nan])},
        market_cap=frame(syms, [np.nan, 1000.0, np.nan]),
        net_debt=frame(syms, [np.nan, 300.0, np.nan]),
        ebitda=frame(syms, [np.nan, 200.0, np.nan]),
    )
    rows = pd.DataFrame({"date": [d] * 3, "symbol": syms, "close": [50.0, 10.0, 5.0]})
    out = valuation_target(rows, vf, [["pe"], ["ev_ebitda"], ["pe", "ev_ebitda"]])
    assert abs(out[0] - 50 * 15 / 20) < 1e-9               # 37.5
    assert abs(out[1] - (8 * 200 - 300) / (1000 / 10)) < 1e-9   # 13.0
    assert np.isnan(out[2])
