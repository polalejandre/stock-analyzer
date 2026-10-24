import numpy as np
import pandas as pd
import pytest

from analyzer.config import load_rules
from analyzer.features.quality import QualityFeatures
from analyzer.features.value import ValueFeatures
from analyzer.panel.fundamentals import FundamentalPanels
from analyzer.signals.gates import eligibility, pick, undervalued, value_trap

RULES = load_rules()
IDX = pd.bdate_range("2024-01-01", periods=1)


def _frame(**cols):
    return pd.DataFrame({k: [v] for k, v in cols.items()}, index=IDX, dtype="float64")


def _quality(**over):
    base = {n: _frame(A=np.nan, B=np.nan, C=np.nan) for n in QualityFeatures.__dataclass_fields__}
    for n in ("revenue_decline_2q", "revenue_decline_known"):
        base[n] = pd.DataFrame(False, index=IDX, columns=["A", "B", "C"])
    base.update(over)
    return QualityFeatures(**base)


VARIANTS = pd.Series({"A": "standard", "B": "financials", "C": "reit"})


def test_margin_rule_applies_to_standard_and_reit_only():
    q = _quality(margin_change=_frame(A=-0.10, B=-0.10, C=-0.02),
                 revenue_growth=_frame(A=0.1, B=0.1, C=0.1),
                 revenue_q_growth=_frame(A=0.1, B=0.1, C=0.1))
    t = value_trap(q, VARIANTS, RULES)
    assert t.fails.iloc[0].tolist() == [True, False, False]


def test_leverage_cap_differs_by_variant():
    q = _quality(nd_ebitda=_frame(A=5.0, B=50.0, C=5.0))
    t = value_trap(q, VARIANTS, RULES)
    assert t.fails.iloc[0].tolist() == [True, False, False]     # REIT cap 7, financials none


def test_unknown_inputs_skip_the_rule_and_are_counted():
    q = _quality()
    t = value_trap(q, VARIANTS, RULES)
    assert not t.fails.any().any()
    assert t.missing.iloc[0].tolist() == [4, 2, 3]   # standard: revenue, margin, fcf, leverage
    assert t.unknown["fcf"].iloc[0].tolist() == [True, False, False]


def test_revenue_rule_needs_ttm_and_two_quarters_down():
    q = _quality(revenue_growth=_frame(A=-0.05, B=0.0, C=0.0),
                 revenue_q_growth=_frame(A=-0.05, B=0.0, C=0.0),
                 revenue_decline_2q=pd.DataFrame([[True, True, False]], index=IDX, columns=list("ABC")),
                 revenue_decline_known=pd.DataFrame([[True, True, True]], index=IDX, columns=list("ABC")))
    assert value_trap(q, VARIANTS, RULES).fails.iloc[0].tolist() == [True, False, False]


def test_guidance_tone_is_not_built_yet():
    import dataclasses
    rules = dataclasses.replace(RULES, gates=dataclasses.replace(RULES.gates, guidance_tone_enabled=True))
    with pytest.raises(NotImplementedError, match="guidance tone"):
        value_trap(_quality(), VARIANTS, rules)


def test_pick_reads_cells_and_tolerates_missing_columns():
    f = pd.DataFrame({"A": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    rows = pd.DataFrame({"symbol": ["A", "Z"], "date": f.index})
    out = pick(f, rows)
    assert out[0] == 1.0 and np.isnan(out[1])


def test_undervalued_counts_metrics_and_peers():
    idx = IDX
    ratio = {"pe": _frame(A=10.0), "ev_ebitda": _frame(A=8.0), "p_fcf": _frame(A=np.nan), "ps": _frame(A=2.0)}
    pct = {"pe": _frame(A=10.0), "ev_ebitda": _frame(A=20.0), "p_fcf": _frame(A=5.0), "ps": _frame(A=40.0)}
    peer = {"pe": _frame(A=15.0), "ev_ebitda": _frame(A=10.0), "p_fcf": _frame(A=1.0), "ps": _frame(A=1.0)}
    vf = ValueFeatures(ratio, pct, pct, peer, _frame(A=1.0), _frame(A=0.0), _frame(A=1.0))
    rows = pd.DataFrame({"symbol": ["A"], "date": idx})
    u = undervalued(rows, vf, RULES)
    assert u.loc[0, "n_metrics"] == 3                 # p_fcf has no ratio
    assert u.loc[0, "median_pct"] == 20.0
    assert u.loc[0, "cheaper"] == 2
    assert u.loc[0, "gate1"]

def test_revenue_rule_is_missing_when_a_quarter_is_missing():
    # TTM growth is known and negative, two quarters "down", but one quarter is unknown
    q = _quality(revenue_growth=_frame(A=-0.05, B=-0.05, C=-0.05),
                 revenue_q_growth=_frame(A=-0.05, B=-0.05, C=-0.05),
                 revenue_decline_2q=pd.DataFrame([[True, True, True]], index=IDX, columns=list("ABC")),
                 revenue_decline_known=pd.DataFrame([[False, True, False]], index=IDX, columns=list("ABC")))
    t = value_trap(q, VARIANTS, RULES)
    assert t.fails.iloc[0].tolist() == [False, True, False]
    assert t.unknown["revenue"].iloc[0].tolist() == [True, False, True]


def test_eligibility_counts_each_skip_reason_once_in_order():
    day = pd.Timestamp("2024-06-03")
    idx = pd.DatetimeIndex([day])
    cols = ["VAR", "HIST", "REQ", "CAP", "OK", "FIN"]
    variants = pd.Series({"VAR": "excluded:fund", "HIST": "standard", "REQ": "standard",
                          "CAP": "standard", "OK": "standard", "FIN": "financials"})
    old = day - pd.Timedelta(days=RULES.features.history_min_days + 30)
    new = day - pd.Timedelta(days=RULES.features.history_min_days - 30)
    first = pd.Series({"VAR": old, "HIST": new, "REQ": old, "CAP": old, "OK": old, "FIN": old},
                      dtype="datetime64[ns]")

    def panel(**v):
        return pd.DataFrame({c: [v.get(c, 1.0)] for c in cols}, index=idx, dtype="float64")

    frames = {"revenue_ttm": panel(REQ=np.nan),
              "operating_income_ttm": panel(FIN=np.nan),     # financials need net income instead
              "net_income_ttm": panel()}
    f = FundamentalPanels(frames, first, pd.Series(False, index=cols), pd.Series(True, index=cols))
    member = pd.DataFrame(True, index=idx, columns=cols)
    e = eligibility(member, variants, f, panel(CAP=np.nan), RULES)
    assert e.skipped == {"variant": 1, "history": 1, "required": 1, "market_cap": 1}
    assert e.eligible.iloc[0].to_dict() == {"VAR": False, "HIST": False, "REQ": False,
                                            "CAP": False, "OK": True, "FIN": True}
    # non-members are never counted
    none = eligibility(member & False, variants, f, panel(CAP=np.nan), RULES)
    assert none.skipped == dict.fromkeys(("variant", "history", "required", "market_cap"), 0)
    assert not none.eligible.any().any()
