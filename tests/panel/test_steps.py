import math

import pandas as pd

from analyzer.data.edgar.quarters import discrete_quarters, quarter_candidates
from analyzer.panel.steps import (
    add_missing_periods,
    annual_steps,
    combine_ttm,
    fallback_ends,
    first_versions,
    instant_steps,
    latest_value_steps,
    quarter_steps,
    sum_periods,
    total_debt,
)

T = pd.Timestamp


def _q(rows):
    """rows: (start, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    df["days"] = (df.period_end - df.period_start).dt.days + 1
    df["derived"] = False
    return df


def _facts(rows, tag="us-gaap:X"):
    """rows: (start or None, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    df["cik"], df["tag"], df["accession"] = 1, tag, [f"a{i}" for i in range(len(df))]
    return df


QUARTERS = _q([
    ("2023-01-01", "2023-03-31", 10, "2023-05-01"), ("2023-04-01", "2023-06-30", 11, "2023-08-01"),
    ("2023-07-01", "2023-09-30", 12, "2023-11-01"), ("2023-10-01", "2023-12-31", 13, "2024-02-15"),
    ("2024-01-01", "2024-03-31", 20, "2024-05-01"),
])


def test_quarter_steps_sum_four_consecutive_quarters():
    s = quarter_steps(QUARTERS).set_index("filed")
    assert math.isnan(s.loc[T("2023-11-01"), "ttm"])           # only three quarters known
    assert s.loc[T("2024-02-15"), "ttm"] == 46
    assert s.loc[T("2024-05-01"), "ttm"] == 56
    assert s.loc[T("2024-05-01"), "q0"] == 20 and s.loc[T("2024-05-01"), "q0_ly"] == 10
    assert s.loc[T("2024-05-01"), "q1"] == 13 and math.isnan(s.loc[T("2024-05-01"), "q1_ly"])


def test_quarter_steps_need_consecutive_quarters():
    gap = QUARTERS.drop(index=1)
    s = quarter_steps(gap).set_index("filed")
    assert math.isnan(s.loc[T("2024-05-01"), "ttm"])


def test_quarter_steps_flag_fallback_quarters():
    s = quarter_steps(QUARTERS, frozenset({T("2023-06-30")})).set_index("filed")
    assert s.loc[T("2024-02-15"), "fallback"]
    assert s.loc[T("2024-05-01"), "fallback"]          # 2023-06-30 is still in the last four


def test_annual_steps_keep_the_first_report():
    f = _facts([("2022-01-01", "2022-12-31", 100, "2023-02-20"),
                ("2022-01-01", "2022-12-31", 90, "2024-02-20"),      # restated a year later
                ("2023-01-01", "2023-12-31", 120, "2024-02-20")])
    s = annual_steps(f).set_index("filed")
    assert s.loc[T("2023-02-20"), "ttm"] == 100
    assert s.loc[T("2024-02-20"), "ttm"] == 120 and s.loc[T("2024-02-20"), "period_end"] == T("2023-12-31")


def test_combine_prefers_newer_annual_over_stale_quarters():
    q = quarter_steps(QUARTERS.iloc[:4])                         # TTM to 2023-12-31
    a = annual_steps(_facts([("2024-01-01", "2024-12-31", 500, "2025-02-20")]))
    c = combine_ttm(q, a).set_index("filed")
    assert c.loc[T("2024-02-15"), "ttm"] == 46 and not c.loc[T("2024-02-15"), "annual"]
    assert c.loc[T("2025-02-20"), "ttm"] == 500 and c.loc[T("2025-02-20"), "annual"]
    assert c.loc[T("2025-02-20"), "value_filed"] == T("2025-02-20")


def test_combine_keeps_the_filed_date_of_the_value_used():
    q = quarter_steps(QUARTERS.iloc[:4])
    a = annual_steps(_facts([("2022-01-01", "2022-12-31", 40, "2023-02-01")]))
    c = combine_ttm(q, a).set_index("filed")
    assert c.loc[T("2023-11-01"), "annual"]                      # quarters incomplete: annual
    assert c.loc[T("2023-11-01"), "value_filed"] == T("2023-02-01")


def test_instant_steps_average_recent_period_ends():
    f = _facts([(None, "2023-03-31", 10, "2023-05-01"), (None, "2023-06-30", 20, "2023-08-01"),
                (None, "2023-06-30", 99, "2024-08-01")])          # later restatement ignored
    s = instant_steps(f).set_index("filed")
    assert s.loc[T("2023-08-01"), "value"] == 20 and s.loc[T("2023-08-01"), "avg5"] == 15
    assert T("2024-08-01") not in s.index


def test_total_debt_counts_the_current_portion_once():
    ltd = _facts([(None, "2023-12-31", 100, "2024-02-01")], tag="us-gaap:LongTermDebt")
    std = _facts([(None, "2023-12-31", 10, "2024-02-01")], tag="us-gaap:LongTermDebtCurrent")
    assert total_debt(ltd, std)["value"].tolist() == [100]
    cp = _facts([(None, "2023-12-31", 5, "2024-02-01")], tag="us-gaap:CommercialPaper")
    assert total_debt(ltd, cp)["value"].tolist() == [105]
    noncurrent = _facts([(None, "2023-12-31", 90, "2024-02-01")], tag="us-gaap:LongTermDebtNoncurrent")
    assert total_debt(noncurrent, std)["value"].tolist() == [100]


def test_bank_revenue_fills_only_missing_periods():
    nii = _q([("2023-01-01", "2023-03-31", 7, "2023-05-01"), ("2023-04-01", "2023-06-30", 7, "2023-08-01")])
    non = _q([("2023-01-01", "2023-03-31", 3, "2023-05-01"), ("2023-04-01", "2023-06-30", 3, "2023-08-02")])
    bank = sum_periods(nii, non)
    assert bank["value"].tolist() == [10, 10]
    assert bank["filed"].tolist() == [T("2023-05-01"), T("2023-08-02")]
    rev = _q([("2023-01-01", "2023-03-31", 11, "2023-05-01")])
    merged = add_missing_periods(rev, bank)
    assert merged.sort_values("period_end")["value"].tolist() == [11, 10]


def test_fallback_ends_detects_summed_rows():
    f = _facts([("2023-01-01", "2023-03-31", 1, "2023-05-01")], tag="us-gaap:A+us-gaap:B")
    assert fallback_ends(f, ()) == frozenset({T("2023-03-31")})


# ---- fix round 1: truncation invariance and review findings ----------------------------------

def _upto(df, d):
    return df[df["filed"] <= d]


def _same(full, part, d):
    exp = full[full["filed"] <= d].reset_index(drop=True)
    pd.testing.assert_frame_equal(exp, part.reset_index(drop=True), check_dtype=False)


def _assert_invariant(build, inputs, cuts):
    """build(*inputs) on inputs cut at each date equals the full result's rows filed <= date."""
    full = build(*inputs)
    for d in map(T, cuts):
        _same(full, build(*[_upto(x, d) for x in inputs]), d)


def test_combine_annual_only_does_not_crash():
    a = annual_steps(_facts([("2023-01-01", "2023-12-31", 120, "2024-02-20")]))
    c = combine_ttm(quarter_steps(QUARTERS.iloc[:0]), a)
    assert c["annual"].tolist() == [True] and c["ttm"].tolist() == [120]
    assert str(c["period_end"].dtype) == "datetime64[ns]"
    assert combine_ttm(quarter_steps(QUARTERS.iloc[:0]), annual_steps(_facts([]))).empty


def test_combine_quarters_only_does_not_crash():
    c = combine_ttm(quarter_steps(QUARTERS), annual_steps(_facts([])))
    assert not c["annual"].any() and c["ttm"].iloc[-1] == 56


def test_combine_annual_needs_more_than_a_quarter_margin():
    q = quarter_steps(QUARTERS.iloc[:4])                          # TTM to 2023-12-31
    a = annual_steps(_facts([("2023-01-01", "2024-01-03", 50, "2024-02-20")]))   # ends 3 days later
    c = combine_ttm(q, a).set_index("filed")
    assert not c.loc[T("2024-02-20"), "annual"] and c.loc[T("2024-02-20"), "ttm"] == 46


FULL_FACTS = _facts([
    ("2022-01-01", "2022-12-31", 100, "2023-02-20"), ("2022-01-01", "2022-12-31", 90, "2024-02-20"),
    ("2023-01-01", "2023-12-31", 120, "2024-02-20"), ("2024-01-01", "2024-12-31", 150, "2025-02-20"),
])
CUTS = ["2023-02-20", "2023-12-01", "2024-02-20", "2024-06-01", "2025-02-20"]


def test_quarter_steps_truncation_invariant():
    _assert_invariant(quarter_steps, [QUARTERS], ["2023-08-01", "2024-02-15", "2024-03-01", "2024-05-01"])


def test_annual_steps_truncation_invariant():
    _assert_invariant(annual_steps, [FULL_FACTS], CUTS)


def test_combine_truncation_invariant():
    def build(q, f):
        return combine_ttm(quarter_steps(q), annual_steps(f))
    _assert_invariant(build, [QUARTERS, FULL_FACTS], CUTS + ["2023-08-01", "2024-05-01"])


def test_instant_steps_truncation_invariant():
    f = _facts([(None, "2023-03-31", 10, "2023-05-01"), (None, "2023-06-30", 20, "2023-08-01"),
                (None, "2023-06-30", 99, "2024-08-01"), (None, "2023-09-30", 30, "2023-11-01")])
    _assert_invariant(instant_steps, [f], ["2023-05-01", "2023-09-01", "2023-11-01", "2024-08-01"])


def test_latest_value_steps_truncation_invariant():
    f = _facts([("2023-01-01", "2023-06-30", 5, "2023-08-01"), ("2023-04-01", "2023-06-30", 4, "2023-08-01"),
                ("2023-01-01", "2023-03-31", 3, "2023-05-01"), ("2023-04-01", "2023-06-30", 8, "2024-08-01"),
                ("2023-01-01", "2023-09-30", 6, "2023-11-01")])
    _assert_invariant(latest_value_steps, [f], ["2023-05-01", "2023-08-01", "2023-11-01", "2024-08-01"])


def test_latest_value_prefers_shortest_duration_regardless_of_order():
    rows = [("2023-01-01", "2023-06-30", 5, "2023-08-01"), ("2023-04-01", "2023-06-30", 4, "2023-08-01"),
            ("2023-01-01", "2023-03-31", 3, "2023-05-01")]
    a = latest_value_steps(_facts(rows)).set_index("filed")
    b = latest_value_steps(_facts(rows[::-1])).set_index("filed")
    assert a.loc[T("2023-08-01"), "value"] == 4
    assert a.loc[T("2023-08-01"), "period_end"] == T("2023-06-30")
    assert a["value"].tolist() == b["value"].tolist() == [3, 4]


def test_debt_known_when_each_line_is_first_filed():
    nc = _facts([(None, "2019-12-31", 100, "2020-02-15")], tag="us-gaap:LongTermDebtNoncurrent")
    cp = _facts([(None, "2019-12-31", 5, "2021-02-15")], tag="us-gaap:CommercialPaper")
    d = total_debt(nc, cp)
    assert d["value"].tolist() == [100, 105]
    assert d["filed"].tolist() == [T("2020-02-15"), T("2021-02-15")]
    s = instant_steps(d, keep_updates=True).set_index("filed")
    assert s.loc[T("2020-02-15"), "value"] == 100 and s.loc[T("2021-02-15"), "value"] == 105
    d2 = total_debt(cp.assign(filed=T("2020-01-01")), nc)         # short line first
    assert d2["value"].tolist() == [5, 105]


def test_overlapping_short_line_does_not_delay_filed():
    ltd = _facts([(None, "2019-12-31", 100, "2020-02-15")], tag="us-gaap:LongTermDebt")
    cur = _facts([(None, "2019-12-31", 10, "2021-02-15")], tag="us-gaap:LongTermDebtCurrent")
    d = total_debt(ltd, cur)
    assert d["value"].tolist() == [100] and d["filed"].tolist() == [T("2020-02-15")]


def test_total_debt_truncation_invariant():
    nc = _facts([(None, "2019-12-31", 100, "2020-02-15"), (None, "2020-12-31", 110, "2021-02-15")],
                tag="us-gaap:LongTermDebtNoncurrent")
    cp = _facts([(None, "2019-12-31", 5, "2021-02-15"), (None, "2020-12-31", 6, "2021-02-15")],
                tag="us-gaap:CommercialPaper")
    _assert_invariant(total_debt, [nc, cp], ["2020-02-15", "2020-12-01", "2021-02-15"])
    ltd = _facts([(None, "2019-12-31", 100, "2020-02-15")], tag="us-gaap:LongTermDebt")
    cur = _facts([(None, "2019-12-31", 10, "2021-02-15")], tag="us-gaap:LongTermDebtCurrent")
    _assert_invariant(total_debt, [ltd, cur], ["2020-02-15", "2021-02-15"])


def test_bank_sum_known_before_late_comparative_primary():
    bank = _q([("2018-01-01", "2018-03-31", 10, "2018-05-01")])
    rev = _q([("2018-01-01", "2018-03-31", 12, "2019-05-01")])    # first reported as a comparative
    merged = add_missing_periods(rev, bank)
    assert sorted(merged["filed"]) == [T("2018-05-01"), T("2019-05-01")]
    s = quarter_steps(merged).set_index("filed")
    assert s.loc[T("2018-05-01"), "q0"] == 10 and s.loc[T("2019-05-01"), "q0"] == 12
    assert len(s) == 2
    later = _q([("2018-01-01", "2018-03-31", 12, "2018-04-01")])  # primary already known: extra dropped
    assert len(add_missing_periods(later, bank)) == 1


def test_bank_sum_and_missing_periods_truncation_invariant():
    nii = _q([("2018-01-01", "2018-03-31", 7, "2018-05-01"), ("2018-04-01", "2018-06-30", 7, "2018-08-01")])
    non = _q([("2018-01-01", "2018-03-31", 3, "2018-05-01"), ("2018-04-01", "2018-06-30", 3, "2018-08-02")])
    rev = _q([("2018-01-01", "2018-04-01", 12, "2019-05-01")]).assign(
        period_end=T("2018-04-02"), days=92)                       # end a day off, filed much later

    def build(n, o, r):
        return add_missing_periods(r, sum_periods(n, o))

    cuts = ["2018-05-01", "2018-08-01", "2018-08-02", "2019-05-01"]
    full = build(nii, non, rev).sort_values(["filed", "period_end"])
    for d in map(T, cuts):
        part = build(_upto(nii, d), _upto(non, d), _upto(rev, d)).sort_values(["filed", "period_end"])
        _same(full, part, d)
    s = sum_periods(nii, non)
    for d in map(T, cuts):
        _same(s, sum_periods(_upto(nii, d), _upto(non, d)), d)


def test_fallback_flag_follows_the_first_filed_version():
    primary_then_fb = pd.concat([
        _facts([("2023-01-01", "2023-03-31", 1, "2023-05-01")], tag="us-gaap:Revenues"),
        _facts([("2023-01-01", "2023-03-31", 1, "2024-05-01")], tag="us-gaap:A+us-gaap:B")], ignore_index=True)
    assert fallback_ends(primary_then_fb, ()) == frozenset()
    fb_first = primary_then_fb.assign(filed=primary_then_fb["filed"].iloc[::-1].to_numpy())
    assert fallback_ends(fb_first, ()) == frozenset({T("2023-03-31")})
    assert fallback_ends(primary_then_fb.iloc[:0], ()) == frozenset()


def test_fallback_ends_truncation_invariant():
    f = pd.concat([
        _facts([("2023-01-01", "2023-03-31", 1, "2023-05-01")], tag="us-gaap:Revenues"),
        _facts([("2023-01-01", "2023-03-31", 1, "2024-05-01")], tag="us-gaap:A+us-gaap:B"),
        _facts([("2023-04-01", "2023-06-30", 1, "2023-08-01")], tag="us-gaap:A+us-gaap:B"),
        _facts([("2023-04-01", "2023-06-30", 1, "2024-08-01")], tag="us-gaap:Revenues")],
        ignore_index=True)
    first_filed = f.groupby("period_end")["filed"].min()
    full = fallback_ends(f, ())
    for d in map(T, ["2023-05-01", "2023-08-01", "2024-05-01", "2024-08-01"]):
        part = fallback_ends(_upto(f, d), ())
        assert part == frozenset(e for e in full if first_filed[e] <= d)


REREPORT = _facts([("2023-01-01", "2023-03-31", 10, "2023-05-01"),
                   ("2023-04-01", "2023-06-30", 11, "2023-08-01"),
                   ("2023-07-01", "2023-09-30", 12, "2023-11-01"),
                   ("2023-10-01", "2023-12-31", 13, "2024-02-15"),
                   ("2023-01-01", "2023-03-31", 10.5, "2024-02-15"),   # 10-K re-reports Q1
                   ("2024-01-01", "2024-03-31", 20, "2024-05-01")])


def test_first_versions_keeps_the_first_report():
    fv = first_versions(REREPORT)
    q1 = fv[fv["period_end"] == T("2023-03-31")]
    assert q1["filed"].tolist() == [T("2023-05-01")] and q1["value"].tolist() == [10]
    assert len(fv) == 5 and list(fv.columns) == list(REREPORT.columns)
    assert first_versions(REREPORT.iloc[::-1])["value"].sum() == fv["value"].sum()


def test_first_version_quarters_truncation_invariant():
    def build(f):
        return quarter_steps(discrete_quarters(first_versions(f)))
    cuts = ["2023-05-01", "2023-11-01", "2024-02-15", "2024-05-01"]
    full = build(REREPORT)
    for d in map(T, cuts):
        _same(full, build(_upto(REREPORT, d)), d)
    assert full.set_index("filed").loc[T("2024-02-15"), "ttm"] == 46


def test_debt_overlap_with_short_line_first_is_truncation_invariant():
    ltd = _facts([(None, "2019-12-31", 100, "2021-02-15")], tag="us-gaap:LongTermDebt")
    cur = _facts([(None, "2019-12-31", 10, "2020-02-15")], tag="us-gaap:LongTermDebtCurrent")
    d = total_debt(ltd, cur)
    assert d["value"].tolist() == [10, 100]
    assert d["filed"].tolist() == [T("2020-02-15"), T("2021-02-15")]
    _assert_invariant(total_debt, [ltd, cur], ["2020-02-15", "2020-06-01", "2021-02-15"])


def test_combine_reports_the_filing_of_the_quarter_step_used():
    q = quarter_steps(QUARTERS.iloc[:4])
    a = annual_steps(_facts([("2024-01-01", "2024-12-31", 500, "2025-02-20")]))
    c = combine_ttm(q, a).set_index("filed")
    last_q = q["filed"].max()
    assert c.loc[T("2025-02-20"), "q_filed"] == last_q           # annual step adds no quarter
    assert c.loc[last_q, "q_filed"] == last_q
    assert combine_ttm(quarter_steps(QUARTERS.iloc[:0]), a)["q_filed"].isna().all()


def test_instant_value_filed_ignores_older_dates_first_reported_later():
    f = _facts([(None, "2023-12-31", 100, "2024-02-01"), (None, "2023-06-30", 90, "2025-03-01")])
    s = instant_steps(f).set_index("filed")
    assert s.loc[T("2025-03-01"), "period_end"] == T("2023-12-31")
    assert s.loc[T("2025-03-01"), "value_filed"] == T("2024-02-01")


# ---- final review I1: a quarter derivable early but reported directly years later ---------------

LATE_DIRECT = _facts([  # June fiscal year (HAIN): Q4 FY2015 derivable from the FY2015 10-K
    ("2014-07-01", "2014-09-30", 10, "2014-11-01"),
    ("2014-10-01", "2014-12-31", 11, "2015-02-01"),
    ("2015-01-01", "2015-03-31", 12, "2015-05-01"),
    ("2014-07-01", "2015-03-31", 33, "2015-05-01"),
    ("2014-07-01", "2015-06-30", 46, "2015-08-21"),   # FY2015 10-K: derived Q4 = 13
    ("2015-04-01", "2015-06-30", 14, "2017-06-22"),   # restated 10-K reports Q4 directly, years later
])


def test_quarter_candidates_keep_direct_and_derived_with_their_own_filing():
    c = quarter_candidates(LATE_DIRECT)
    q4 = c[c["period_end"] == T("2015-06-30")]
    assert q4["derived"].tolist() == [True, False]
    assert q4["filed"].tolist() == [T("2015-08-21"), T("2017-06-22")]
    assert q4["value"].tolist() == [13, 14]
    assert c["filed"].is_monotonic_increasing
    q = discrete_quarters(LATE_DIRECT)                     # the audit view is unchanged
    assert q[q["period_end"] == T("2015-06-30")]["derived"].tolist() == [False]


def test_late_direct_quarter_replaces_the_derived_one_only_from_its_filing():
    s = quarter_steps(quarter_candidates(LATE_DIRECT)).set_index("filed")
    assert s.loc[T("2015-08-21"), "ttm"] == 46 and s.loc[T("2015-08-21"), "q0"] == 13
    assert s.loc[T("2017-06-22"), "q0"] == 14 and s.loc[T("2017-06-22"), "ttm"] == 47


def test_late_direct_quarter_is_truncation_invariant():
    def build(f):
        return quarter_steps(quarter_candidates(f))
    _assert_invariant(build, [LATE_DIRECT], ["2015-05-01", "2015-08-21", "2016-03-01", "2017-06-22"])


def test_same_day_direct_quarter_wins_over_derived():
    f = _facts([("2023-01-01", "2023-03-31", 10, "2023-05-01"),
                ("2023-04-01", "2023-06-30", 11, "2023-08-01"),
                ("2023-01-01", "2023-06-30", 22, "2023-08-01")])   # derived Q2 = 12, same filing
    s = quarter_steps(quarter_candidates(f)).set_index("filed")
    assert s.loc[T("2023-08-01"), "q0"] == 11
    assert quarter_steps(quarter_candidates(f.iloc[::-1])).equals(quarter_steps(quarter_candidates(f)))


def test_drifted_start_refiling_of_a_link_does_not_move_the_derived_quarter():
    # RBC: the FY 10-K gives Q4 = FY - 9M; years later a comparative re-files FY with a start one
    # day off. Point in time the first-filed link of each period end is used.
    f = _facts([("2017-04-02", "2017-12-30", 495, "2018-02-06"),
                ("2017-04-02", "2018-03-31", 675, "2018-05-30"),
                ("2017-04-01", "2018-03-31", 675, "2020-05-20")])
    c = quarter_candidates(f)
    assert c["filed"].tolist() == [T("2018-05-30")] and c["value"].tolist() == [180]
    _assert_invariant(lambda x: quarter_steps(quarter_candidates(x)), [f],
                      ["2018-05-30", "2019-01-01", "2020-05-20"])


def test_same_day_candidates_for_one_quarter_do_not_depend_on_input_order():
    # SUN: one 10-K tags Q4 twice, a 92-day quarter and a 97-day stub; the shortest period wins
    f = _facts([("2012-09-26", "2012-12-31", 9150, "2013-03-29"),
                ("2012-10-01", "2012-12-31", 8576, "2013-03-29")])
    for rows in (f, f.iloc[::-1]):
        s = quarter_steps(quarter_candidates(rows))
        assert s["q0"].tolist() == [8576]
