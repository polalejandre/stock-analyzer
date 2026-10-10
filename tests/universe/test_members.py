from datetime import date

import pandas as pd
import pytest

from analyzer.config import UniverseRules
from analyzer.data.price_sync import replace_prices
from analyzer.store.db import connect, migrate
from analyzer.universe.members import Member, price_symbols, universe_on, young_cutoff
from tests.helpers import bars

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=20, young_max_years=5,
                      young_min_market_cap=2e9)
D = date(2026, 10, 9)


def liquid(conn, ticker, close=50.0, volume=1_000_000.0, n=30):
    replace_prices(conn, ticker, bars(pd.bdate_range(end=D, periods=n), close, volume=volume),
                   "yfinance")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.executemany("INSERT INTO index_membership VALUES (?,?,?,?)", [
        ("sp500", "AAPL", "2010-01-01", None),
        ("sp400", "MID", "2015-01-01", None),
        ("sp500", "THINIDX", "2010-01-01", None),
        ("sp500", "GONE", "2010-01-01", "2020-01-01"),
    ])
    c.executemany("INSERT INTO tickers (ticker, cik, updated_at) VALUES (?,?,?)", [
        ("AAPL", 320193, "x"), ("MID", 2, "x"), ("THINIDX", 3, "x")])
    c.executemany("INSERT INTO young_listings VALUES (?,?,?,?)", [
        ("BIGNEW", 100, "2024-01-02", "2026-10-09"),
        ("SMALLNEW", 101, "2024-01-02", "2026-10-09"),
        ("SPACCO", 102, "2024-01-02", "2026-10-09"),
        ("AGED", 103, "2019-06-03", "2026-10-09"),
    ])
    c.execute("INSERT INTO companies VALUES (102, 'Spac', '6770', 'Blank Checks', 'x')")
    shares = [(100, 1e8), (101, 1e7), (102, 1e9), (103, 1e9)]  # × $50 close
    c.executemany(
        "INSERT INTO facts VALUES (?, 'shares_outstanding', 'dei:EntityCommonStockSharesOutstanding',"
        " 'shares', '', '2026-06-30', ?, '2026-08-01', '10-Q', 'acc' || ?, 'companyfacts')",
        [(cik, n, cik) for cik, n in shares])
    for t in ("AAPL", "MID", "BIGNEW", "SMALLNEW", "SPACCO", "AGED"):
        liquid(c, t)
    liquid(c, "THINIDX", volume=1_000.0)
    return c


def test_universe_on_applies_all_filters(conn):
    members = universe_on(conn, D, RULES)
    assert members == [
        Member("AAPL", 320193, "sp500"),
        Member("BIGNEW", 100, "young"),   # $5B cap
        Member("MID", 2, "sp400"),
    ]
    # SMALLNEW: $500M cap. SPACCO: blank check. AGED: listed > 5 years before D.
    # THINIDX: illiquid. GONE: left the index.


def test_universe_on_a_past_date(conn):
    assert [m.ticker for m in universe_on(conn, date(2026, 6, 1), RULES)] == []  # no prices yet


def test_price_symbols_split_active_and_historical(conn):
    active, historical = price_symbols(conn, D, RULES)
    assert active == ["AAPL", "BIGNEW", "MID", "SMALLNEW", "SPACCO", "THINIDX"]
    assert historical == ["AGED", "GONE"]


# --- beyond the brief: point-in-time and boundary behavior ------------------------------------

def add_young(conn, ticker, cik, first_trade, shares=None, filed="2026-08-01", prices=True,
              close=50.0, volume=1_000_000.0, n=30):
    conn.execute("INSERT INTO young_listings VALUES (?,?,?,?)", (ticker, cik, first_trade, "x"))
    if shares is not None:
        add_shares(conn, cik, shares, filed)
    if prices:
        liquid(conn, ticker, close=close, volume=volume, n=n)


def add_shares(conn, cik, value, filed, concept="shares_outstanding", period_end="2026-06-30"):
    tag = {"shares_outstanding": "dei:EntityCommonStockSharesOutstanding",
           "diluted_shares": "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"}[concept]
    start = "2026-04-01" if concept == "diluted_shares" else ""
    conn.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (cik, concept, tag, "shares", start, period_end, value, filed, "10-Q",
                  f"acc-{cik}-{filed}-{value}", "companyfacts"))


def tickers(conn, d=D):
    return [m.ticker for m in universe_on(conn, d, RULES)]


def test_young_cutoff_is_five_years_back():
    assert young_cutoff(date(2026, 10, 9), RULES) == date(2021, 10, 9)  # 1826 days (one leap day)


def test_young_window_is_open_at_the_old_end_and_closed_at_d(conn):
    cutoff = young_cutoff(D, RULES)
    add_young(conn, "ONCUT", 200, cutoff.isoformat(), shares=1e8)               # exactly 5y: out
    add_young(conn, "AFTCUT", 201, (cutoff + pd.Timedelta(days=1)).isoformat(), shares=1e8)
    add_young(conn, "TODAYIPO", 202, D.isoformat(), shares=1e8)
    got = tickers(conn)
    assert "ONCUT" not in got
    assert "AFTCUT" in got
    assert "TODAYIPO" in got


def test_young_listing_is_invisible_before_its_first_trade_date(conn):
    add_young(conn, "LATER", 200, "2026-10-01", shares=1e8, filed="2026-09-01")
    assert "LATER" not in tickers(conn, date(2026, 9, 30))
    assert "LATER" in tickers(conn, date(2026, 10, 1))


def test_market_cap_threshold_is_inclusive(conn):
    add_young(conn, "EXACT", 200, "2025-01-02", shares=4e7)       # 4e7 x $50 = $2.0B
    add_young(conn, "JUSTUNDER", 201, "2025-01-02", shares=3_999_999)
    got = tickers(conn)
    assert "EXACT" in got
    assert "JUSTUNDER" not in got


def test_market_cap_uses_shares_known_on_d_not_later_filings(conn):
    # shares jump from 1e7 ($500M) to 1e8 ($5B) in a filing made after d
    add_young(conn, "GROWS", 200, "2025-01-02")
    add_shares(conn, 200, 1e7, "2026-08-01")
    add_shares(conn, 200, 1e8, "2026-10-08", period_end="2026-09-30")
    assert "GROWS" not in tickers(conn, date(2026, 10, 7))
    assert "GROWS" in tickers(conn, date(2026, 10, 8))     # filed that day = known that day


def test_market_cap_without_any_known_shares_excludes(conn):
    add_young(conn, "NOSHARES", 200, "2025-01-02")
    add_young(conn, "FUTURESH", 201, "2025-01-02", shares=1e9, filed="2026-10-09")
    assert "NOSHARES" not in tickers(conn)
    assert "FUTURESH" not in tickers(conn, date(2026, 10, 8))


def test_market_cap_falls_back_to_diluted_shares(conn):
    add_young(conn, "DILONLY", 200, "2025-01-02")
    add_shares(conn, 200, 1e8, "2026-08-01", concept="diluted_shares")
    assert "DILONLY" in tickers(conn)


def test_market_cap_uses_close_on_or_before_d_not_later_closes(conn):
    # price is $10 until 2026-10-01 (cap $1B), then $100 ($10B)
    dates = pd.bdate_range(end=D, periods=30)
    closes = [10.0 if d < pd.Timestamp("2026-10-01") else 100.0 for d in dates]
    conn.execute("INSERT INTO young_listings VALUES ('PUMP', 200, '2025-01-02', 'x')")
    add_shares(conn, 200, 1e8, "2026-08-01")
    replace_prices(conn, "PUMP", bars(dates, closes, volume=10_000_000.0), "yfinance")
    assert "PUMP" not in tickers(conn, date(2026, 9, 30))
    assert "PUMP" in tickers(conn, date(2026, 10, 2))


def test_market_cap_uses_latest_bar_when_d_is_not_a_trading_day(conn):
    add_young(conn, "WKND", 200, "2025-01-02", shares=1e8)          # last bar is Fri 2026-10-09
    assert "WKND" in tickers(conn, date(2026, 10, 11))             # Sunday


def test_cik_merges_reach_the_share_lookup(conn):
    add_young(conn, "MERGED", 200, "2025-01-02")
    add_shares(conn, 300, 1e8, "2026-08-01")                        # filed under the old CIK
    assert "MERGED" not in tickers(conn)
    assert [m.ticker for m in universe_on(conn, D, RULES, merges={200: [300]})
            if m.ticker == "MERGED"] == ["MERGED"]


def test_liquidity_uses_the_last_n_bars_on_or_before_d(conn):
    # 20 thin bars through 2026-09-30, then heavy bars after: thin on 09-30, liquid on 10-09.
    dates = pd.bdate_range(end=D, periods=40)
    vols = [1_000.0 if d <= pd.Timestamp("2026-09-30") else 5_000_000.0 for d in dates]
    conn.execute("INSERT INTO index_membership VALUES ('sp500','SWING','2010-01-01',NULL)")
    f = bars(dates, 50.0)
    f["volume"] = vols
    replace_prices(conn, "SWING", f, "yfinance")
    assert "SWING" not in tickers(conn, date(2026, 9, 30))
    assert "SWING" in tickers(conn, D)           # 5M x $50 on all of the last 20 bars


def test_liquidity_needs_the_full_window_of_bars(conn):
    conn.executemany("INSERT INTO index_membership VALUES (?,?,?,?)",
                     [("sp500", "NINETEEN", "2010-01-01", None),
                      ("sp500", "TWENTY", "2010-01-01", None)])
    liquid(conn, "NINETEEN", volume=100_000_000.0, n=19)
    liquid(conn, "TWENTY", volume=100_000_000.0, n=20)
    got = tickers(conn)
    assert "NINETEEN" not in got
    assert "TWENTY" in got


def test_liquidity_threshold_is_inclusive_and_null_volume_counts_as_zero(conn):
    conn.executemany("INSERT INTO index_membership VALUES (?,?,?,?)",
                     [("sp500", "EXACT", "2010-01-01", None),
                      ("sp500", "NULLVOL", "2010-01-01", None),
                      ("sp500", "UNDER", "2010-01-01", None)])
    liquid(conn, "EXACT", close=25.0, volume=1_000_000.0)       # $25M exactly
    liquid(conn, "UNDER", close=25.0, volume=999_999.0)
    f = bars(pd.bdate_range(end=D, periods=30), 25.0, volume=1_000_000.0)
    f.loc[f.index[-1], "volume"] = float("nan")                 # stored NULL -> 0 in the average
    replace_prices(conn, "NULLVOL", f, "yfinance")
    got = tickers(conn)
    assert "EXACT" in got
    assert "UNDER" not in got
    assert "NULLVOL" not in got


def test_index_membership_wins_when_a_ticker_is_also_a_young_listing(conn):
    conn.execute("INSERT INTO young_listings VALUES ('BOTH', 200, '2025-01-02', 'x')")
    add_shares(conn, 200, 1e8, "2026-01-15")
    conn.execute("INSERT INTO tickers (ticker, cik, updated_at) VALUES ('BOTH', 200, 'x')")
    conn.execute("INSERT INTO index_membership VALUES ('sp400','BOTH','2026-09-01',NULL)")
    liquid(conn, "BOTH", n=120)
    pick = lambda d: [m for m in universe_on(conn, d, RULES) if m.ticker == "BOTH"]  # noqa: E731
    assert pick(date(2026, 8, 31)) == [Member("BOTH", 200, "young")]   # not yet in the index
    assert pick(date(2026, 9, 1)) == [Member("BOTH", 200, "sp400")]    # index wins from then on


def test_sp500_wins_over_sp400_for_a_ticker_in_both(conn):
    conn.execute("INSERT INTO index_membership VALUES ('sp400','AAPL','2010-01-01',NULL)")
    assert [m for m in universe_on(conn, D, RULES) if m.ticker == "AAPL"] == [
        Member("AAPL", 320193, "sp500")]


def test_index_member_without_a_tickers_row_has_no_cik(conn):
    conn.execute("INSERT INTO index_membership VALUES ('sp500','NOCIK','2010-01-01',NULL)")
    liquid(conn, "NOCIK")
    assert [m for m in universe_on(conn, D, RULES) if m.ticker == "NOCIK"] == [
        Member("NOCIK", None, "sp500")]


def test_young_listing_without_a_cik_never_qualifies(conn):
    conn.execute("INSERT INTO young_listings VALUES ('NOCIKY', NULL, '2025-01-02', 'x')")
    liquid(conn, "NOCIKY")
    assert "NOCIKY" not in tickers(conn)


def test_young_listing_without_a_company_row_is_not_a_blank_check(conn):
    add_young(conn, "NOCOMP", 200, "2025-01-02", shares=1e8)    # no companies row for 200
    assert "NOCOMP" in tickers(conn)


def test_index_membership_end_date_is_exclusive(conn):
    assert "GONE" not in tickers(conn, date(2020, 1, 1))   # left on 2020-01-01: no prices anyway
    conn.execute("INSERT INTO index_membership VALUES ('sp500','LEAVER','2010-01-01','2026-10-09')")
    liquid(conn, "LEAVER")
    assert "LEAVER" not in tickers(conn, D)
    assert "LEAVER" in tickers(conn, date(2026, 10, 8))


def test_price_symbols_active_window_follows_today(conn):
    # ten years on, BIGNEW and the others have aged out of the 5-year window
    active, historical = price_symbols(conn, date(2031, 10, 9), RULES)
    assert active == ["AAPL", "MID", "THINIDX"]
    assert historical == ["AGED", "BIGNEW", "GONE", "SMALLNEW", "SPACCO"]
