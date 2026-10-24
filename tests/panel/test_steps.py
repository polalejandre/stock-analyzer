import math

import pandas as pd

from analyzer.panel.steps import (
    add_missing_periods,
    annual_steps,
    combine_ttm,
    fallback_ends,
    instant_steps,
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
