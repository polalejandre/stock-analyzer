import numpy as np
import pandas as pd

from analyzer.features.value import own_percentile, peer_median, ratios
from analyzer.panel.fundamentals import FIELDS, FLAGS, FundamentalPanels
from tests.panel_fixtures import assert_truncation_invariant, cut


def _fund(index, columns, **values):
    frames = {n: pd.DataFrame(np.nan, index=index, columns=columns, dtype="float32") for n in FIELDS}
    frames.update({n: pd.DataFrame(False, index=index, columns=columns) for n in FLAGS})
    for name, v in values.items():
        frames[name] = pd.DataFrame(v, index=index, columns=columns, dtype="float32")
    empty = pd.Series(dtype=bool)
    return FundamentalPanels(frames, pd.Series(dtype="datetime64[ns]"), empty, empty)


def test_ratios_drop_non_positive_denominators():
    idx = pd.bdate_range("2024-01-01", periods=2)
    close = pd.DataFrame({"A": [10.0, 10.0]}, index=idx)
    f = _fund(idx, ["A"], shares=100.0, net_income_ttm=[[50.0], [-5.0]], revenue_ttm=200.0,
              operating_income_ttm=40.0, da_ttm=10.0, ocf_ttm=60.0, capex_ttm=20.0,
              equity=500.0, debt=300.0, cash=100.0)
    r = ratios(close, f)
    assert r["pe"]["A"].tolist()[0] == 20.0 and np.isnan(r["pe"]["A"].tolist()[1])
    assert r["ps"]["A"].iloc[0] == 5.0
    assert r["ev_ebitda"]["A"].iloc[0] == (1000 + 200) / 50
    assert r["p_fcf"]["A"].iloc[0] == 1000 / 40
    assert r["pb"]["A"].iloc[0] == 2.0
    assert r["p_ffo"]["A"].iloc[0] == 1000 / 60


def test_own_percentile_needs_min_history_and_ranks_today():
    s = pd.DataFrame({"A": np.arange(1.0, 11.0)}, index=pd.bdate_range("2024-01-01", periods=10))
    p = own_percentile(s, window=5, min_obs=3)
    assert p["A"].iloc[:2].isna().all()
    assert p["A"].iloc[-1] == 100.0
    s.iloc[-1] = 0.0
    assert own_percentile(s, 5, 3)["A"].iloc[-1] == 20.0


def test_peer_median_falls_back_to_sector_for_small_industries():
    idx = pd.bdate_range("2024-01-01", periods=1)
    cols = ["A", "B", "C", "D"]
    r = pd.DataFrame([[10.0, 20.0, 30.0, 40.0]], index=idx, columns=cols)
    eligible = pd.DataFrame(True, index=idx, columns=cols)
    ent = pd.DataFrame({"industry": ["i1", "i1", "i1", "i2"], "sector": ["s"] * 4}, index=cols)
    m = peer_median(r, eligible, ent, min_size=3)
    assert m.loc[idx[0], "A"] == 20.0        # i1 has 3 members
    assert m.loc[idx[0], "D"] == 25.0        # i2 has 1: sector median of all four


def test_peer_median_ignores_ineligible_stocks():
    idx = pd.bdate_range("2024-01-01", periods=1)
    r = pd.DataFrame([[10.0, 1000.0]], index=idx, columns=["A", "B"])
    eligible = pd.DataFrame([[True, False]], index=idx, columns=["A", "B"])
    ent = pd.DataFrame({"industry": ["i", "i"], "sector": ["s", "s"]}, index=["A", "B"])
    assert peer_median(r, eligible, ent, min_size=1).loc[idx[0], "A"] == 10.0


def test_percentile_is_truncation_invariant():
    rng = np.random.default_rng(1)
    s = pd.DataFrame(rng.normal(10, 2, size=(60, 3)), index=pd.bdate_range("2024-01-01", periods=60),
                     columns=list("ABC"))
    assert_truncation_invariant(lambda end: own_percentile(cut(s, end), 20, 10),
                                [s.index[15], s.index[40]])


def test_peer_median_is_truncation_invariant():
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2024-01-01", periods=40)
    cols = list("ABCDE")
    r = pd.DataFrame(rng.normal(10, 2, size=(40, 5)), index=idx, columns=cols)
    r.iloc[::7, 1] = np.nan
    eligible = pd.DataFrame(rng.random((40, 5)) > 0.2, index=idx, columns=cols)
    ent = pd.DataFrame({"industry": ["i1", "i1", "i1", "i2", "i2"], "sector": ["s"] * 5}, index=cols)
    assert_truncation_invariant(
        lambda end: peer_median(cut(r, end), cut(eligible, end), ent, min_size=3),
        [idx[10], idx[25]])
