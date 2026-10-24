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


def _rules(window=3, min_obs=2, peer=2):
    from types import SimpleNamespace
    return SimpleNamespace(percentile_window=window, percentile_min_obs=min_obs, peer_min_size=peer)


def _vf_inputs(n=6):
    idx = pd.bdate_range("2024-01-01", periods=n)
    cols = ["A", "B", "C"]
    close = pd.DataFrame(10.0, index=idx, columns=cols)
    close["A"] = np.arange(10.0, 10.0 + n)
    f = _fund(idx, cols, shares=100.0, net_income_ttm=50.0, revenue_ttm=200.0,
              operating_income_ttm=40.0, da_ttm=10.0, ocf_ttm=60.0, capex_ttm=20.0,
              equity=500.0, debt=300.0, cash=100.0)
    eligible = pd.DataFrame(True, index=idx, columns=cols)
    ent = pd.DataFrame({"industry": ["i", "i", "i"], "sector": ["s"] * 3}, index=cols)
    # standard = A, B ; financials = C ; pb used by financials, no one uses p_ffo
    columns_for = {"pe": ["A", "B", "C"], "ev_ebitda": ["A", "B"], "p_fcf": ["A", "B"],
                   "ps": ["A", "B"], "pb": ["C"]}
    return idx, close, f, eligible, ent, columns_for


def test_value_features_restricts_columns_and_known_values():
    from analyzer.features.value import value_features
    idx, close, f, eligible, ent, cf = _vf_inputs()
    v = value_features(close, f, eligible, ent, cf, _rules())
    assert "p_ffo" not in v.ratio and "p_ffo" not in v.pct and "p_ffo" not in v.peer
    for m, cols in cf.items():
        for d in (v.ratio, v.pct, v.p50, v.peer):
            assert list(d[m].columns) == cols and d[m].shape == (len(idx), len(cols))
    # pe: A = 10*100/50 = 20 on day 0, rising 2 per day; B constant 20
    assert v.ratio["pe"]["A"].iloc[0] == 20.0
    # window 3, last 3 values of A are 26,28,30 -> p50 = 28; today's rank = 100
    assert v.p50["pe"]["A"].iloc[-1] == 28.0
    assert v.pct["pe"]["A"].iloc[-1] == 100.0
    assert v.pct["pe"]["A"].iloc[0] != v.pct["pe"]["A"].iloc[0]  # below min_obs
    # peers of pe on last day: A=30, B=20, C=20 -> median 20
    assert v.peer["pe"]["A"].iloc[-1] == 20.0
    # peer pb only over C
    assert v.peer["pb"]["C"].iloc[-1] == v.ratio["pb"]["C"].iloc[-1] == 2.0
    assert v.market_cap["A"].iloc[0] == 1000.0
    assert v.ebitda["A"].iloc[0] == 50.0
    assert v.net_debt["A"].iloc[0] == 200.0


def test_net_debt_nan_when_equity_missing_and_nonpositive_ev_gives_nan():
    from analyzer.features.value import net_debt
    idx = pd.bdate_range("2024-01-01", periods=1)
    f = _fund(idx, ["A", "B"], shares=1.0, debt=0.0, cash=1000.0, operating_income_ttm=40.0,
              da_ttm=10.0, equity=[[500.0, np.nan]])
    assert net_debt(f)["A"].iloc[0] == -1000.0 and np.isnan(net_debt(f)["B"].iloc[0])
    close = pd.DataFrame({"A": [10.0], "B": [10.0]}, index=idx)
    r = ratios(close, f)
    assert np.isnan(r["ev_ebitda"]["A"].iloc[0])  # EV = 10 - 1000 < 0


def test_ratios_with_columns_for_matches_full():
    idx, close, f, _, _, cf = _vf_inputs()
    full, part = ratios(close, f), ratios(close, f, cf)
    assert set(part) == set(cf)
    for m, cols in cf.items():
        pd.testing.assert_frame_equal(part[m], full[m][cols])


def test_value_features_is_truncation_invariant():
    from analyzer.features.value import value_features
    idx, close, f, eligible, ent, cf = _vf_inputs(n=30)
    close = close * (1 + 0.01 * np.arange(30))[:, None]

    def compute(end):
        fc = FundamentalPanels({k: cut(v, end) for k, v in f.frames.items()}, f.first_filed,
                               f.has_property, f.has_us_gaap)
        v = value_features(cut(close, end), fc, cut(eligible, end), ent, cf, _rules(10, 5, 2))
        out = {}
        for name in ("ratio", "pct", "p50", "peer"):
            for m, df in getattr(v, name).items():
                out[f"{name}.{m}"] = df
        out.update(market_cap=v.market_cap, net_debt=v.net_debt, ebitda=v.ebitda)
        return out

    assert_truncation_invariant(compute, [idx[12], idx[20]])
