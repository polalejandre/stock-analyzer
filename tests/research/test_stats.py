import warnings
from dataclasses import replace

import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.research.stats import bootstrap_ci, breakdowns, is_win, quintiles, summarize, verdict

R = load_rules()


def _events(xs, start="2022-01-03"):
    n = len(xs)
    return pd.DataFrame({"date": pd.bdate_range(start, periods=n, freq="7D"),
                         "ret_63": np.array(xs) + 0.01, "spy_63": 0.01, "xs_spy_63": xs,
                         "xs_ref_63": xs, "mae": -0.05, "score": np.linspace(50, 90, n)})


def test_summary_counts_only_complete_windows():
    ev = _events([0.1, -0.05, 0.02, np.nan])
    s = summarize(ev, R)
    assert s["n"] == 3 and abs(s["median_xs_spy"] - 0.02) < 1e-12
    assert abs(s["win_rate"] - 2 / 3) < 1e-12


def test_summary_of_empty_frame_is_nan_without_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = summarize(_events([]), R)
    assert s["n"] == 0
    for k in ("median_ret", "median_spy", "median_xs_spy", "median_xs_ref", "win_rate", "median_mae"):
        assert np.isnan(s[k])
    assert all(np.isnan(v) for v in s["ci_median_xs_spy"] + s["ci_win_rate"])


def test_bootstrap_is_reproducible_and_brackets_the_median():
    rng = np.random.default_rng(0)
    values = rng.normal(0.02, 0.05, 400)
    months = np.repeat(np.arange(40), 10)
    lo, hi = bootstrap_ci(values, months, np.median, 500, seed=7)
    assert lo < np.median(values) < hi
    assert (lo, hi) == bootstrap_ci(values, months, np.median, 500, seed=7)


def test_quintiles_rank_by_score():
    ev = _events(list(np.linspace(-0.1, 0.1, 50)))
    q = quintiles(ev, R)
    assert q["quintile"].tolist() == [1, 2, 3, 4, 5]
    assert q["median_xs_spy"].is_monotonic_increasing


def test_is_win_for_both_bases():
    ev = _events([0.02, -0.005, -0.02])  # raw = xs + 0.01 -> 0.03, 0.005, -0.01
    assert is_win(ev, R).tolist() == [True, False, False]
    raw = replace(R, research=replace(R.research, win_basis="raw"))
    assert is_win(ev, raw).tolist() == [True, True, False]


def test_breakdowns_two_columns():
    ev = _events([0.1, 0.3, -0.2, 0.2, -0.1, 0.4])
    ev["sector"] = ["A", "A", "A", "B", "B", "B"]
    ev["regime"] = ["up", "up", "dn", "up", "dn", "up"]
    b = breakdowns(ev, ["sector", "regime", "missing"], R).set_index(["column", "value"])
    assert len(b) == 4
    assert b.loc[("sector", "A"), "n"] == 3
    assert abs(b.loc[("sector", "A"), "median_xs_spy"] - 0.1) < 1e-12
    assert abs(b.loc[("sector", "A"), "win_rate"] - 2 / 3) < 1e-12
    assert abs(b.loc[("sector", "B"), "median_xs_spy"] - 0.2) < 1e-12
    assert abs(b.loc[("regime", "up"), "median_xs_spy"] - 0.25) < 1e-12
    assert b.loc[("regime", "up"), "win_rate"] == 1.0
    assert abs(b.loc[("regime", "dn"), "median_xs_spy"] + 0.15) < 1e-12
    assert b.loc[("regime", "dn"), "win_rate"] == 0.0


def test_verdict_needs_every_test():
    good = {"n": 150, "median_xs_spy": 0.03, "win_rate": 0.6}
    group2 = {"median_xs_spy": 0.01}
    q = pd.DataFrame({"quintile": [1, 5], "n": [30, 30], "median_xs_spy": [0.0, 0.05]})
    assert verdict(good, group2, q, R)["passed"]
    v = verdict({**good, "n": 99}, group2, q, R)
    assert not v["passed"] and v["tests"] == {"n": False, "median_xs_spy": True, "win_rate": True,
                                               "beats_dislocated": True, "quintiles": True}


def test_verdict_fails_when_top_quintile_not_above_bottom():
    good = {"n": 150, "median_xs_spy": 0.03, "win_rate": 0.6}
    q = pd.DataFrame({"quintile": [1, 5], "n": [30, 30], "median_xs_spy": [0.05, 0.05]})
    v = verdict(good, {"median_xs_spy": 0.01}, q, R)
    assert not v["passed"] and v["tests"]["quintiles"] is False
    assert all(val for k, val in v["tests"].items() if k != "quintiles")
