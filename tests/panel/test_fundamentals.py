import math

import pandas as pd
import pytest

from analyzer.config import load_rules
from analyzer.panel.fundamentals import load_fundamental_panels
from analyzer.store.db import connect, migrate
from tests.panel_fixtures import assert_truncation_invariant

RULES = load_rules().features


def _db(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    return conn


def _fact(conn, concept, tag, start, end, value, filed, accn=None):
    conn.execute("INSERT INTO facts VALUES (1, ?, ?, 'USD', ?, ?, ?, ?, '10-Q', ?, 'companyfacts')",
                 (concept, tag, start or "", end, value, filed, accn or f"{concept}-{end}-{filed}"))


def _quarters(conn, concept, tag, values, first_filed="2020-05-01"):
    ends = pd.date_range("2020-03-31", periods=len(values), freq="QE")
    filed = pd.date_range(first_filed, periods=len(values), freq="QS-FEB") + pd.Timedelta(days=0)
    for end, f, v in zip(ends, filed, values, strict=True):
        start = (end - pd.offsets.QuarterEnd() + pd.Timedelta(days=1)).date().isoformat()
        _fact(conn, concept, tag, start, end.date().isoformat(), v, f.date().isoformat())


def _entities():
    return pd.DataFrame({"cik": [1.0]}, index=pd.Index(["AAA"], name="symbol"))


def test_value_appears_on_its_filed_date(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 10, 10, 10])
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    rev = f["revenue_ttm"]["AAA"]
    last_filed = rev.first_valid_index()
    assert rev.loc[last_filed] == 40
    assert rev.loc[:last_filed].iloc[:-1].isna().all()


def test_ttm_goes_stale_after_max_age(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 10, 10, 10])
    dates = pd.bdate_range("2020-01-01", "2023-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    rev = f["revenue_ttm"]["AAA"]
    first = rev.first_valid_index()
    assert rev.loc[first + pd.Timedelta(days=RULES.ttm_max_age_days - 30):].iloc[0] == 40
    assert rev.loc[first + pd.Timedelta(days=RULES.ttm_max_age_days + 30):].isna().all()


def test_market_cap_is_continuous_across_split(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", None,
          "2024-03-31", 100.0, "2024-05-01")
    conn.execute("INSERT INTO splits VALUES ('AAA', '2024-06-10', 2.0)")  # 2-for-1 after the count
    dates = pd.bdate_range("2024-05-01", "2024-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    # Stored closes are on the post-split basis everywhere, so shares must be too: 200.
    assert f["shares"]["AAA"].dropna().unique().tolist() == [200.0]


def test_cover_shares_fall_back_to_diluted_when_stale(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", None,
          "2020-03-31", 100.0, "2020-05-01")
    _fact(conn, "diluted_shares", "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
          "2022-01-01", "2022-03-31", 120.0, "2022-05-01")
    dates = pd.bdate_range("2022-05-02", "2022-05-06")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["shares"]["AAA"].tolist() == [120.0] * 5
    assert f["diluted_shares"]["AAA"].all()


def test_bank_revenue_fills_missing_revenue(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "net_interest_income", "us-gaap:InterestIncomeExpenseNet", [7, 7, 7, 7])
    _quarters(conn, "noninterest_income", "us-gaap:NoninterestIncome", [3, 3, 3, 3])
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["revenue_ttm"]["AAA"].dropna().iloc[-1] == 40


def test_negative_derived_quarter_is_dropped(tmp_path):
    conn = _db(tmp_path)
    # 9M YTD 300, FY 280: a derived Q4 of -20 is impossible for revenue.
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-03-31", 100, "2020-05-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-06-30", 200, "2020-08-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-09-30", 300, "2020-11-01")
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-12-31", 280, "2021-02-15")
    dates = pd.bdate_range("2021-02-15", "2021-02-19")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["revenue_q0"]["AAA"].iloc[0] == 100          # Q3; the negative Q4 is gone
    assert f["revenue_ttm"]["AAA"].iloc[0] == 280         # annual fallback
    assert f["annual"]["AAA"].iloc[0]


def test_static_series(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10])
    _fact(conn, "real_estate_property", "us-gaap:RealEstateInvestmentPropertyNet", None,
          "2020-03-31", 5.0, "2020-05-01")
    f = load_fundamental_panels(conn, _entities(), pd.bdate_range("2020-01-01", periods=5), RULES)
    assert f.has_property["AAA"] and f.has_us_gaap["AAA"]
    assert f.first_filed["AAA"] == pd.Timestamp("2020-05-01")


def test_panels_are_truncation_invariant(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 11, 12, 13, 14, 15])
    _quarters(conn, "net_income", "us-gaap:NetIncomeLoss", [1, 2, 3, 4, 5, 6])
    _quarters(conn, "net_interest_income", "us-gaap:InterestIncomeExpenseNet", [7, 7, 7, 7, 8, 8])
    _quarters(conn, "noninterest_income", "us-gaap:NoninterestIncome", [3, 3, 3, 3, 4, 4])
    _quarters(conn, "stockholders_equity", "us-gaap:StockholdersEquity", [50, 55, 60, 65, 70, 75])
    _fact(conn, "long_term_debt", "us-gaap:LongTermDebt", None, "2020-03-31", 30.0, "2020-05-01")
    _fact(conn, "short_term_debt", "us-gaap:DebtCurrent", None, "2020-03-31", 5.0, "2020-08-01")
    _fact(conn, "shares_outstanding", "dei:EntityCommonStockSharesOutstanding", None,
          "2020-03-31", 100.0, "2020-05-01")
    _fact(conn, "diluted_shares", "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
          "2020-07-01", "2020-09-30", 120.0, "2020-11-01")
    # a later re-report of an early quarter must not change what was known earlier
    _fact(conn, "revenue", "us-gaap:Revenues", "2020-01-01", "2020-03-31", 99, "2021-03-01")
    conn.execute("INSERT INTO splits VALUES ('AAA', '2020-09-10', 2.0)")
    conn.commit()
    dates = pd.bdate_range("2020-01-01", "2022-06-30")

    def compute(end):
        if end is None:
            return load_fundamental_panels(conn, _entities(), dates, RULES).frames
        cut_db = connect(tmp_path / f"cut-{end.date()}.db")
        migrate(cut_db)
        for table in ("facts", "splits", "tickers"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if table == "facts":
                rows = [r for r in rows if r[7] <= end.date().isoformat()]
            if rows:
                cut_db.executemany(
                    f"INSERT INTO {table} VALUES ({', '.join('?' * len(rows[0]))})", rows)
        cut_db.commit()
        return load_fundamental_panels(cut_db, _entities(), dates, RULES).upto(end).frames

    assert_truncation_invariant(compute, ["2020-06-15", "2020-09-01", "2020-12-01",
                                          "2021-03-15", "2021-09-01"])


def test_quarter_fields_age_on_their_own_filing(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 10, 10, 10])   # last filed 2021-02-01
    for year in (2021, 2022, 2023):                                     # 10-Ks, no new quarter
        _fact(conn, "revenue", "us-gaap:Revenues", f"{year}-01-01", f"{year}-12-31", 50,
              f"{year + 1}-02-15")
    dates = pd.bdate_range("2021-03-01", "2024-03-05")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    assert f["revenue_q0"]["AAA"].loc["2021-03-01"] == 10
    for name in ("revenue_q0", "revenue_q0_ly", "revenue_q1", "revenue_q1_ly"):
        assert math.isnan(f[name]["AAA"].loc["2024-03-01"])
    assert f["revenue_ttm"]["AAA"].loc["2024-03-01"] == 50


def test_instant_fields_age_on_their_own_filing(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "stockholders_equity", "us-gaap:StockholdersEquity", None, "2020-12-31", 100.0,
          "2021-02-01")
    # an older balance-sheet date first reported years later must not refresh the value
    _fact(conn, "stockholders_equity", "us-gaap:StockholdersEquity", None, "2020-06-30", 90.0,
          "2023-03-01")
    f = load_fundamental_panels(conn, _entities(), pd.bdate_range("2023-03-01", "2023-03-10"), RULES)
    assert f["equity"]["AAA"].isna().all()


def test_equity_avg_is_mean_of_recent_balance_sheets(tmp_path):
    conn = _db(tmp_path)
    for end, v, filed in [("2020-03-31", 100.0, "2020-05-01"), ("2020-06-30", 110.0, "2020-08-01"),
                          ("2020-09-30", 120.0, "2020-11-01")]:
        _fact(conn, "stockholders_equity", "us-gaap:StockholdersEquity", None, end, v, filed)
    f = load_fundamental_panels(conn, _entities(), pd.bdate_range("2020-11-02", periods=3), RULES)
    assert f["equity"]["AAA"].tolist() == [120.0] * 3
    assert f["equity_avg"]["AAA"].tolist() == pytest.approx([110.0] * 3)


def test_diluted_shares_not_double_adjusted_for_split_before_filing(tmp_path):
    conn = _db(tmp_path)
    _fact(conn, "diluted_shares", "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
          "2022-01-01", "2022-03-31", 120.0, "2022-05-01")
    conn.execute("INSERT INTO splits VALUES ('AAA', '2022-04-15', 2.0)")  # after period, before filing
    f = load_fundamental_panels(conn, _entities(), pd.bdate_range("2022-05-02", periods=3), RULES)
    assert f["shares"]["AAA"].tolist() == [120.0] * 3


def test_summed_fallback_first_report_sets_flag_from_its_filing(tmp_path):
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:SalesRevenueNet+us-gaap:Other", [10, 10, 10, 10])
    dates = pd.bdate_range("2020-01-01", "2021-06-30")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    flag = f["fallback"]["AAA"]
    assert flag.loc["2020-05-01"]                       # the first summed report's filing date
    assert not flag.loc[:"2020-04-30"].any()
    assert flag.loc[f["revenue_ttm"]["AAA"].first_valid_index()]


def test_failed_companies_are_counted_in_one_warning(tmp_path, caplog, monkeypatch):
    from analyzer.panel import fundamentals

    def boom(*a, **k):
        raise RuntimeError("bad")

    monkeypatch.setattr(fundamentals, "company_steps", boom)
    with caplog.at_level("WARNING"):
        load_fundamental_panels(_db(tmp_path), _entities(), pd.bdate_range("2020-01-01", periods=3),
                                RULES)
    assert any("failed for 1 of 1" in r.getMessage() and "AAA" in r.getMessage()
               for r in caplog.records)


def test_quarter_reported_directly_years_later_keeps_its_early_derived_value(tmp_path):
    """HAIN: Q4 FY2015 is derivable from the FY2015 10-K; a restated 10-K filed in 2017 reports it
    directly. Before 2017 the derived value must be used, with or without the later filing."""
    conn = _db(tmp_path)
    for start, end, value, filed in [
            ("2014-07-01", "2014-09-30", 10, "2014-11-01"), ("2014-10-01", "2014-12-31", 11, "2015-02-01"),
            ("2015-01-01", "2015-03-31", 12, "2015-05-01"), ("2014-07-01", "2015-03-31", 33, "2015-05-01"),
            ("2014-07-01", "2015-06-30", 46, "2015-08-21"), ("2015-04-01", "2015-06-30", 14, "2017-06-22")]:
        _fact(conn, "revenue", "us-gaap:Revenues", start, end, value, filed)
    conn.commit()
    dates = pd.bdate_range("2015-01-01", "2017-12-29")
    full = load_fundamental_panels(conn, _entities(), dates, RULES)
    rev = full["revenue_ttm"]["AAA"]
    assert rev.loc["2016-01-04"] == 46 and not full["annual"]["AAA"].loc["2016-01-04"]
    assert full["revenue_q0"]["AAA"].loc["2016-01-04"] == 13
    assert full["revenue_q0"]["AAA"].loc["2017-06-22"] == 14

    def compute(end):
        if end is None:
            return full.frames
        cut_db = _db(tmp_path / f"c{end.date()}")
        rows = [r for r in conn.execute("SELECT * FROM facts") if r[7] <= end.date().isoformat()]
        cut_db.executemany(f"INSERT INTO facts VALUES ({', '.join('?' * len(rows[0]))})", rows)
        cut_db.commit()
        return load_fundamental_panels(cut_db, _entities(), dates, RULES).upto(end).frames

    assert_truncation_invariant(compute, ["2015-09-01", "2016-01-04", "2017-06-22"])


def test_yoy_growth_pairs_the_ttm_with_the_four_quarters_a_year_earlier(tmp_path):
    """Filings stop after eight quarters: a year later the TTM is still the last four quarters
    (within the age limit). Growth must compare them with the four before, not with themselves."""
    from analyzer.features.quality import quality_features
    from analyzer.features.value import net_debt
    rules = load_rules()
    conn = _db(tmp_path)
    _quarters(conn, "revenue", "us-gaap:Revenues", [10, 11, 12, 13, 14, 15, 16, 17])
    _quarters(conn, "operating_income", "us-gaap:OperatingIncomeLoss", [1, 1, 1, 1, 2, 2, 2, 2])
    conn.commit()
    dates = pd.bdate_range("2021-01-01", "2023-03-31")
    f = load_fundamental_panels(conn, _entities(), dates, RULES)
    d = pd.Timestamp("2023-03-01")                       # > 1 year after the last filing
    assert f["revenue_ttm"]["AAA"].loc[d] == 62 and f["revenue_ttm_ly"]["AAA"].loc[d] == 46
    q = quality_features(f, net_debt(f), rules.features, rules.score)
    assert abs(q.revenue_growth["AAA"].loc[d] - (62 / 46 - 1)) < 1e-6
    assert abs(q.margin_change["AAA"].loc[d] - (8 / 62 - 4 / 46)) < 1e-6
    first = f["revenue_ttm"]["AAA"].first_valid_index()
    assert math.isnan(q.revenue_growth["AAA"].loc[first])   # no year-earlier TTM yet
