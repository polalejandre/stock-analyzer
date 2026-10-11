import pandas as pd
import pytest

from analyzer.data.edgar.quarters import discrete_quarters


def frame(rows):
    """rows: (start, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


COLUMN_DTYPES = {
    "period_start": "datetime64[ns]",
    "period_end": "datetime64[ns]",
    "value": "float64",
    "filed": "datetime64[ns]",
    "days": "int64",
    "derived": "bool",
}


def assert_typed(q):
    assert {c: str(t) for c, t in q.dtypes.items()} == COLUMN_DTYPES


def assert_empty_and_typed(q):
    assert q.empty
    assert_typed(q)


def test_calendar_year_from_ytd_chain():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-01", "2025-09-30", 420, "2025-11-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),
    ]))
    assert q["value"].tolist() == [100, 150, 170, 180]
    assert q["derived"].tolist() == [False, True, True, True]
    assert q["filed"].iloc[3] == pd.Timestamp("2026-02-15")


def test_direct_quarter_wins_over_derived():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-04-01", "2025-06-30", 155, "2025-08-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
    ]))
    assert q["value"].tolist() == [100, 155]
    assert q["derived"].tolist() == [False, False]


def test_costco_style_16_week_fourth_quarter():
    q = discrete_quarters(frame([
        ("2025-09-01", "2025-11-23", 60, "2025-12-15"),    # 12 weeks
        ("2025-09-01", "2026-02-15", 125, "2026-03-10"),   # 24 weeks YTD
        ("2025-09-01", "2026-05-10", 190, "2026-06-01"),   # 36 weeks YTD
        ("2025-09-01", "2026-08-30", 280, "2026-10-08"),   # 52 weeks
    ]))
    assert q["value"].tolist() == [60, 65, 65, 90]
    assert q["days"].tolist()[-1] == 112


def test_53_week_year_fourth_quarter_is_kept():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-09-27", 300, "2025-11-01"),   # YTD spanning 269 days
        ("2025-01-01", "2026-01-24", 450, "2026-03-01"),   # YTD spanning 388 days, ends 17 weeks later
    ]))
    assert q["value"].tolist() == [150]
    assert q["derived"].tolist() == [True]
    assert q["days"].tolist() == [119]


def test_gap_that_is_not_a_quarter_is_ignored():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),   # 9-month gap: not derivable
    ]))
    assert q["value"].tolist() == [100]


def test_non_additive_uses_direct_quarters_only():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 1.0, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 2.1, "2025-08-01"),
    ]), additive=False)
    assert q["value"].tolist() == [1.0]


def test_empty_input():
    assert_empty_and_typed(discrete_quarters(frame([])))


def test_days_is_the_inclusive_period_length_for_direct_and_derived_quarters():
    q = discrete_quarters(frame([
        ("2025-09-01", "2025-11-23", 60, "2025-12-15"),    # direct 12 weeks
        ("2025-09-01", "2026-02-15", 125, "2026-03-10"),
        ("2025-09-01", "2026-05-10", 190, "2026-06-01"),
        ("2025-09-01", "2026-08-30", 280, "2026-10-08"),
    ]))
    # a 12-week quarter is 84 days whether it is a direct fact or a YTD difference
    assert q["days"].tolist() == [84, 84, 84, 112]


def test_derived_filed_is_the_later_of_its_two_components():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-09-01"),   # re-filed after the half-year report
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
    ]))
    assert q["derived"].tolist() == [False, True]
    assert q["filed"].tolist() == [pd.Timestamp("2025-09-01"), pd.Timestamp("2025-09-01")]


def test_instant_facts_without_a_start_date_yield_nothing():
    df = frame([(None, "2025-03-31", 5000, "2025-05-01")])
    assert_empty_and_typed(discrete_quarters(df))


def test_non_additive_without_direct_quarters_is_empty_and_typed():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-06-30", 2.1, "2025-08-01"),
        ("2025-01-01", "2025-09-30", 3.3, "2025-11-01"),
    ]), additive=False)
    assert_empty_and_typed(q)


def test_non_empty_result_has_the_same_typed_columns():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
    ]))
    assert list(q.columns) == list(COLUMN_DTYPES)
    assert_typed(q)


@pytest.mark.parametrize("direct_end", ["2025-09-29", "2025-10-01"])
def test_direct_and_derived_quarter_ending_a_day_apart_are_one_quarter(direct_end):
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-01", "2025-09-30", 420, "2025-11-01"),      # would derive Q3 = 170 ending 09-30
        ("2025-07-01", direct_end, 171, "2025-11-01"),        # the direct Q3, one day off
    ]))
    assert q["period_end"].tolist() == [
        pd.Timestamp("2025-03-31"), pd.Timestamp("2025-06-30"), pd.Timestamp(direct_end),
    ]
    assert q["value"].tolist() == [100, 150, 171]
    assert q["derived"].tolist() == [False, True, False]


def test_equal_kind_near_duplicates_keep_the_later_filed():
    q = discrete_quarters(frame([
        ("2025-07-01", "2025-09-29", 170, "2025-11-01"),
        ("2025-07-02", "2025-09-30", 172, "2025-11-15"),      # re-filed, a day off
    ]))
    assert q["value"].tolist() == [172]
    assert q["filed"].tolist() == [pd.Timestamp("2025-11-15")]


@pytest.mark.parametrize("drift", [1, 3])
def test_ytd_starts_that_drift_by_a_few_days_still_chain(drift):
    ytd_start = f"2025-01-{1 + drift:02d}"
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        (ytd_start, "2025-06-30", 250, "2025-08-01"),
        (ytd_start, "2025-09-30", 420, "2025-11-01"),
        (ytd_start, "2025-12-31", 600, "2026-02-15"),
    ]))
    assert q["value"].tolist() == [100, 150, 170, 180]
    assert q["derived"].tolist() == [False, True, True, True]


def test_ytd_starts_more_than_three_days_apart_do_not_chain():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-05", "2025-06-30", 250, "2025-08-01"),
    ]))
    assert q["value"].tolist() == [100]


def test_same_period_end_under_drifting_starts_uses_the_latest_filing():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-02", "2025-06-30", 252, "2025-09-01"),      # restated H1, start drifted a day
    ]))
    assert q["value"].tolist() == [100, 152]
    assert q["filed"].tolist() == [pd.Timestamp("2025-05-01"), pd.Timestamp("2025-09-01")]


def test_ten_k_only_year_derives_q4_from_nine_months_and_full_year():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-09-30", 420, "2025-11-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),
    ]))
    assert q["period_end"].tolist() == [pd.Timestamp("2025-12-31")]
    assert q["value"].tolist() == [180]
    assert q["derived"].tolist() == [True]
    assert q["filed"].tolist() == [pd.Timestamp("2026-02-15")]


def test_missing_nine_month_link_gives_q1_and_q2_but_no_bogus_q4():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),      # 6M to 12M is a 184-day gap
    ]))
    assert q["period_end"].tolist() == [pd.Timestamp("2025-03-31"), pd.Timestamp("2025-06-30")]
    assert q["value"].tolist() == [100, 150]


def test_two_fiscal_years_give_eight_quarters():
    q = discrete_quarters(frame([
        ("2024-01-01", "2024-03-31", 90, "2024-05-01"),
        ("2024-01-01", "2024-06-30", 190, "2024-08-01"),
        ("2024-01-01", "2024-09-30", 300, "2024-11-01"),
        ("2024-01-01", "2024-12-31", 420, "2025-02-15"),
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-01", "2025-09-30", 420, "2025-11-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),
    ]))
    assert q["value"].tolist() == [90, 100, 110, 120, 100, 150, 170, 180]
    assert q["derived"].tolist() == [False, True, True, True, False, True, True, True]
    assert q["period_end"].is_monotonic_increasing and q["period_end"].is_unique


def test_later_restated_annual_is_not_mixed_with_original_nine_months():
    # BAX: the FY2023 value was recast in a 2026 10-K; the 9-month value never was.
    q = discrete_quarters(frame([
        ("2023-01-01", "2023-09-30", 502, "2023-11-02"),
        ("2023-01-01", "2023-12-31", 678, "2024-02-09"),   # as first reported
        ("2023-01-01", "2023-12-31", 432, "2026-02-12"),   # recast comparative, years later
    ]))
    assert q["value"].tolist() == [176]
    assert q["filed"].tolist() == [pd.Timestamp("2024-02-09")]


def test_later_restated_direct_quarter_does_not_replace_the_first_report():
    q = discrete_quarters(frame([
        ("2023-01-01", "2023-03-31", 100, "2023-05-01"),
        ("2023-01-01", "2023-03-31", 80, "2024-05-01"),    # recast comparative in the next year's Q1
        ("2023-01-01", "2023-06-30", 250, "2023-08-01"),
    ]))
    assert q["value"].tolist() == [100, 150]
    assert q["filed"].tolist() == [pd.Timestamp("2023-05-01"), pd.Timestamp("2023-08-01")]


def test_amendment_within_a_year_of_period_end_wins():
    q = discrete_quarters(frame([
        ("2023-01-01", "2023-09-30", 420, "2023-11-01"),
        ("2023-01-01", "2023-12-31", 600, "2024-02-15"),
        ("2023-01-01", "2023-12-31", 610, "2024-06-01"),   # 10-K/A
    ]))
    assert q["value"].tolist() == [190]
    assert q["filed"].tolist() == [pd.Timestamp("2024-06-01")]


def test_period_known_only_from_a_late_filing_is_still_used():
    q = discrete_quarters(frame([
        ("2023-01-01", "2023-09-30", 420, "2025-11-01"),   # only a comparative exists
        ("2023-01-01", "2023-12-31", 600, "2024-02-15"),
    ]))
    assert q["value"].tolist() == [180]
    assert q["filed"].tolist() == [pd.Timestamp("2025-11-01")]
