import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.quality import balance_score, quality_features
from analyzer.features.value import net_debt
from analyzer.panel.fundamentals import FIELDS, FLAGS, FundamentalPanels
from tests.panel_fixtures import assert_truncation_invariant

RULES = load_rules()
YEAR = RULES.features.year_days


def _fund(n, **series):
    idx = pd.bdate_range("2020-01-01", periods=n)
    frames = {k: pd.DataFrame(np.nan, index=idx, columns=["A"], dtype="float32") for k in FIELDS}
    frames.update({k: pd.DataFrame(False, index=idx, columns=["A"]) for k in FLAGS})
    for k, v in series.items():
        frames[k] = pd.DataFrame({"A": v}, index=idx, dtype="float32")
    e = pd.Series(dtype=bool)
    return FundamentalPanels(frames, pd.Series(dtype="datetime64[ns]"), e, e)


def _q(f):
    return quality_features(f, net_debt(f), RULES.features, RULES.score)


def test_growth_and_margin_change_compare_with_a_year_earlier():
    q = _q(_fund(1, revenue_ttm=[120.0], revenue_ttm_ly=[100.0], operating_income_ttm=[6.0],
                 operating_income_ttm_ly=[10.0]))
    assert abs(q.revenue_growth["A"].iloc[-1] - 0.20) < 1e-6
    assert abs(q.margin_change["A"].iloc[-1] - (0.05 - 0.10)) < 1e-6
    assert abs(q.growth_score["A"].iloc[-1] - 1.0) < 1e-6        # +20% = full credit


def test_revenue_decline_needs_both_quarters_down():
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[11.0], revenue_q1_ly=[10.0]))
    assert not q.revenue_decline_2q["A"].iloc[0]
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[9.0], revenue_q1_ly=[10.0]))
    assert q.revenue_decline_2q["A"].iloc[0]
    assert q.revenue_decline_known["A"].iloc[0]


def test_revenue_decline_known_is_false_when_any_quarter_is_missing():
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[np.nan],
                 revenue_q1_ly=[10.0]))
    assert not q.revenue_decline_2q["A"].iloc[0]
    assert not q.revenue_decline_known["A"].iloc[0]
    assert q.revenue_decline_2q.dtypes.iloc[0] == np.bool_
    assert q.revenue_decline_known.dtypes.iloc[0] == np.bool_


def test_net_debt_to_ebitda_handles_negative_ebitda():
    q = _q(_fund(2, operating_income_ttm=[-5.0, 10.0], da_ttm=[1.0, 0.0], debt=[100.0, 100.0],
                 cash=[0.0, 0.0], equity=[50.0, 50.0]))
    assert np.isinf(q.nd_ebitda["A"].iloc[0])        # debt but no earnings: fails any cap
    assert q.nd_ebitda["A"].iloc[1] == 10.0


def _fcf(now, year_ago):
    return _q(_fund(1, ocf_ttm=[now], ocf_ttm_ly=[year_ago], capex_ttm=[0.0], capex_ttm_ly=[0.0]))


def test_fcf_score_levels():
    assert _fcf(20.0, 10.0).fcf_score["A"].iloc[-1] == 1.0
    assert _fcf(-20.0, -10.0).fcf_score["A"].iloc[-1] == 0.0


def test_fcf_score_middle_levels_and_unknown_prior_year():
    assert _fcf(10.0, 20.0).fcf_score["A"].iloc[-1] == 0.5           # positive but falling
    assert _fcf(-10.0, -20.0).fcf_score["A"].iloc[-1] == 0.25        # negative but improving
    assert np.isnan(_fcf(10.0, np.nan).fcf_score["A"].iloc[-1])      # unknown prior year
    q = _q(_fund(1))
    assert np.isnan(q.fcf_score["A"].iloc[-1])


def test_nd_ebitda_is_nan_when_inputs_are_missing():
    q = _q(_fund(2, operating_income_ttm=[np.nan, 10.0], da_ttm=[0.0, 0.0],
                 debt=[100.0, 100.0], cash=[0.0, 0.0], equity=[50.0, np.nan]))
    assert np.isnan(q.nd_ebitda["A"].iloc[0])        # ebitda unknown
    assert np.isnan(q.nd_ebitda["A"].iloc[1])        # no balance sheet: net debt unknown


def test_known_values_for_eps_roe_and_margin_scores():
    q = _q(_fund(1, eps_ttm=[2.5], eps_ttm_ly=[2.0], net_income_ttm=[6.25],
                 net_income_ttm_ly=[5.0], equity_avg=[50.0], equity_avg_ly=[50.0],
                 revenue_ttm=[100.0], revenue_ttm_ly=[100.0], operating_income_ttm=[8.0],
                 operating_income_ttm_ly=[10.0]))
    assert abs(q.eps_growth["A"].iloc[-1] - 0.25) < 1e-6
    assert abs(q.roe_change["A"].iloc[-1] - 0.025) < 1e-6
    assert abs(q.roe_score["A"].iloc[-1] - 0.75) < 1e-6
    assert abs(q.margin_score["A"].iloc[-1] - 0.3) < 1e-6


def test_outputs_are_float32_except_the_flags():
    q = _q(_fund(YEAR + 1, revenue_ttm=[100.0] * (YEAR + 1)))
    for name in ("revenue_growth", "revenue_q_growth", "eps_growth", "margin_change", "fcf",
                 "fcf_prev", "nd_ebitda", "roe_change", "growth_score", "margin_score",
                 "roe_score", "fcf_score"):
        assert (getattr(q, name).dtypes == np.float32).all(), name


def test_balance_score_is_linear_between_one_and_the_cap():
    s = balance_score(pd.DataFrame({"A": [0.5, 2.5, 4.0, np.inf]}), cap=4.0)
    assert s["A"].tolist() == [1.0, 0.5, 0.0, 0.0]


def test_quality_is_truncation_invariant():
    n = YEAR + 30
    rng = np.random.default_rng(2)
    f = _fund(n, revenue_ttm=rng.uniform(90, 110, n), operating_income_ttm=rng.uniform(5, 15, n),
              net_income_ttm=rng.uniform(1, 9, n), equity_avg=[50.0] * n, equity=[50.0] * n,
              eps_ttm=rng.uniform(1, 3, n), ocf_ttm=rng.uniform(-5, 20, n),
              capex_ttm=rng.uniform(0, 5, n), revenue_q0=rng.uniform(20, 30, n),
              revenue_q0_ly=rng.uniform(20, 30, n), revenue_q1=rng.uniform(20, 30, n),
              revenue_q1_ly=rng.uniform(20, 30, n), da_ttm=rng.uniform(0, 2, n),
              debt=rng.uniform(0, 80, n), cash=rng.uniform(0, 20, n),
              **{f"{p}_ttm_ly": rng.uniform(1, 100, n) for p in
                 ("revenue", "operating_income", "net_income", "eps", "ocf", "capex", "da")},
              equity_avg_ly=rng.uniform(40, 60, n))
    d = f["revenue_ttm"].index
    assert_truncation_invariant(lambda end: _q(f if end is None else f.upto(end)),
                                [d[YEAR + 5], d[YEAR + 20]])


def test_year_ago_values_come_from_the_year_ago_fields_not_a_shifted_panel():
    # filings stopped: the TTM is unchanged for over a year, which a shift(252) would read as
    # "no growth" by comparing the period with itself; the year-ago field has the prior year
    n = YEAR + 1
    q = _q(_fund(n, revenue_ttm=[110.0] * n, revenue_ttm_ly=[100.0] * n,
                 operating_income_ttm=[11.0] * n, operating_income_ttm_ly=[10.0] * n))
    assert abs(q.revenue_growth["A"].iloc[-1] - 0.10) < 1e-6
    assert abs(q.margin_change["A"].iloc[-1]) < 1e-6
