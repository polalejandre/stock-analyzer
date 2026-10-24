import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.quality import balance_score, quality_features
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
    from analyzer.features.value import net_debt
    return quality_features(f, net_debt(f), RULES.features, RULES.score)


def test_growth_and_margin_change_compare_with_a_year_earlier():
    n = YEAR + 1
    rev = [100.0] * YEAR + [120.0]
    op = [10.0] * YEAR + [6.0]
    q = _q(_fund(n, revenue_ttm=rev, operating_income_ttm=op))
    assert abs(q.revenue_growth["A"].iloc[-1] - 0.20) < 1e-6
    assert abs(q.margin_change["A"].iloc[-1] - (0.05 - 0.10)) < 1e-6
    assert abs(q.growth_score["A"].iloc[-1] - 1.0) < 1e-6        # +20% = full credit


def test_revenue_decline_needs_both_quarters_down():
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[11.0], revenue_q1_ly=[10.0]))
    assert not q.revenue_decline_2q["A"].iloc[0]
    q = _q(_fund(1, revenue_q0=[9.0], revenue_q0_ly=[10.0], revenue_q1=[9.0], revenue_q1_ly=[10.0]))
    assert q.revenue_decline_2q["A"].iloc[0]


def test_net_debt_to_ebitda_handles_negative_ebitda():
    q = _q(_fund(2, operating_income_ttm=[-5.0, 10.0], da_ttm=[1.0, 0.0], debt=[100.0, 100.0],
                 cash=[0.0, 0.0], equity=[50.0, 50.0]))
    assert np.isinf(q.nd_ebitda["A"].iloc[0])        # debt but no earnings: fails any cap
    assert q.nd_ebitda["A"].iloc[1] == 10.0


def test_fcf_score_levels():
    n = YEAR + 1
    q = _q(_fund(n, ocf_ttm=[10.0] * YEAR + [20.0], capex_ttm=[0.0] * n))
    assert q.fcf_score["A"].iloc[-1] == 1.0
    q = _q(_fund(n, ocf_ttm=[-10.0] * YEAR + [-20.0], capex_ttm=[0.0] * n))
    assert q.fcf_score["A"].iloc[-1] == 0.0


def test_balance_score_is_linear_between_one_and_the_cap():
    s = balance_score(pd.DataFrame({"A": [0.5, 2.5, 4.0, np.inf]}), cap=4.0)
    assert s["A"].tolist() == [1.0, 0.5, 0.0, 0.0]


def test_quality_is_truncation_invariant():
    n = YEAR + 30
    rng = np.random.default_rng(2)
    f = _fund(n, revenue_ttm=rng.uniform(90, 110, n), operating_income_ttm=rng.uniform(5, 15, n),
              net_income_ttm=rng.uniform(1, 9, n), equity_avg=[50.0] * n, equity=[50.0] * n)
    d = f["revenue_ttm"].index
    assert_truncation_invariant(lambda end: _q(f if end is None else f.upto(end)),
                                [d[YEAR + 5], d[YEAR + 20]])
