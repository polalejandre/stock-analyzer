import pandas as pd

from analyzer.data.edgar.quarters import discrete_quarters


def frame(rows):
    """rows: (start, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


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
        ("2025-01-01", "2025-09-27", 300, "2025-11-01"),   # 39 weeks YTD
        ("2025-01-01", "2026-01-24", 450, "2026-03-01"),   # 17 weeks after the 39-week YTD
    ]))
    assert q["value"].tolist() == [150]
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
    assert discrete_quarters(frame([])).empty


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
    q = discrete_quarters(df)
    assert q.empty
    assert list(q.columns) == ["period_start", "period_end", "value", "filed", "days", "derived"]
