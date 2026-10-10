from datetime import date, timedelta

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import read_prices, replace_prices, write_prices
from analyzer.store.db import connect, migrate
from analyzer.universe.young import average_dollar_volume, screen_young_listings, young_candidates
from tests.helpers import FakeProvider, bars

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=20, young_max_years=5,
                      young_min_market_cap=2e9)


def ticker_map(rows):
    return pd.DataFrame(rows, columns=["cik", "name", "ticker", "exchange"])


def test_young_candidates_filters_exchange_index_and_derivative_tickers():
    tm = ticker_map([
        (1, "A", "NEWCO", "Nasdaq"), (2, "B", "NEWCW", "Nasdaq"), (3, "C", "ABC-PA", "NYSE"),
        (4, "D", "AAPL", "Nasdaq"), (5, "E", "OTCX", "OTC"), (6, "F", "BRK-B", "NYSE"),
        (7, "G", "SPAC-U", "NYSE"),
    ])
    assert young_candidates(tm, exclude={"AAPL"}) == ["BRK-B", "NEWCO"]


def test_average_dollar_volume():
    f = bars(pd.bdate_range("2026-09-01", periods=25), [10.0] * 25, volume=3_000_000.0)
    assert average_dollar_volume(f, 20) == 30_000_000.0


def days(n, end="2026-10-09"):
    return pd.bdate_range(end=end, periods=n)


def test_screen_keeps_liquid_recent_listings(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq"), (11, "Thin", "THIN", "Nasdaq"),
                     (12, "Old", "OLDCO", "NYSE")])
    data = {
        "NEWCO": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0),
        "THIN": bars(days(60), 5.0, volume=100_000.0),
        "OLDCO": bars(pd.bdate_range("2009-01-02", "2026-10-09"), 40.0, volume=1_000_000.0),
    }
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES,
                                 date(2026, 10, 9))
    assert kept == ["NEWCO"]
    assert conn.execute("SELECT ticker, cik, first_trade_date FROM young_listings").fetchall() == [
        ("NEWCO", 10, "2023-03-01")]
    assert read_prices(conn, "NEWCO")["date"].min() == pd.Timestamp("2023-03-01")


# --- beyond the brief: edge cases the screen must survive -------------------------------------

TODAY = date(2026, 10, 9)


def fresh_conn(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


def test_average_dollar_volume_uses_only_the_last_n_bars_and_treats_missing_volume_as_zero():
    f = bars(pd.bdate_range("2026-09-01", periods=25), [10.0] * 25, volume=3_000_000.0)
    f.loc[:4, "volume"] = 1e12  # old bars outside the 20-day window
    assert average_dollar_volume(f, 20) == 30_000_000.0
    f.loc[f.index[-1], "volume"] = float("nan")  # one of the 20 has no volume: counts as 0
    assert average_dollar_volume(f, 20) == (19 * 30_000_000.0) / 20
    assert pd.isna(average_dollar_volume(f.iloc[:0], 20))


def test_young_candidates_ignores_blank_exchange_and_keeps_sorted_unique():
    tm = ticker_map([(1, "A", "ZED", "NYSE"), (2, "B", "ALPHA", "Nasdaq"), (3, "C", "NOEX", ""),
                     (4, "D", "ALPHA", "Nasdaq")])
    assert young_candidates(tm, exclude=set()) == ["ALPHA", "ZED"]


def test_screen_with_no_provider_data_stores_nothing(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    kept = screen_young_listings(conn, FakeProvider("yfinance", {}), tm, RULES, TODAY)
    assert kept == []
    assert conn.execute("SELECT COUNT(*) FROM young_listings").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone() == (0,)


def test_screen_with_empty_ticker_map_stores_nothing(tmp_path):
    conn = fresh_conn(tmp_path)
    assert screen_young_listings(conn, FakeProvider("yfinance", {}), ticker_map([]), RULES,
                                 TODAY) == []


def test_screen_does_not_fetch_derivative_tickers(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq"), (11, "Pref", "ABC-PA", "NYSE"),
                     (12, "War", "NEWCW", "Nasdaq"), (13, "Otc", "OTCX", "OTC")])
    provider = FakeProvider("yfinance", {})
    screen_young_listings(conn, provider, tm, RULES, TODAY)
    assert provider.calls[0][0] == ("NEWCO",)


def test_young_candidates_exclude_defaults_to_nothing():
    tm = ticker_map([(1, "A", "AAPL", "Nasdaq"), (2, "B", "NEWCO", "Nasdaq")])
    assert young_candidates(tm) == ["AAPL", "NEWCO"]
    assert young_candidates(tm, exclude={"AAPL"}) == ["NEWCO"]


def test_screen_skips_a_listing_whose_history_is_empty_or_unusable(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "A", "EMPTYX", "Nasdaq"), (11, "B", "NANCL", "Nasdaq"),
                     (12, "C", "GOODX", "Nasdaq")])
    nan_close = bars(days(60), 50.0, volume=1_000_000.0)

    class Provider(FakeProvider):
        def fetch(self, symbols, start, end):
            out = super().fetch(symbols, start, end)
            if (end - start).days > 100:  # the full-history call: hand back unusable frames
                out["EMPTYX"] = nan_close.iloc[:0]
                out["NANCL"] = nan_close.assign(close=float("nan"))
            return out

    data = {t: bars(days(60), 50.0, volume=1_000_000.0) for t in ("EMPTYX", "NANCL", "GOODX")}
    kept = screen_young_listings(conn, Provider("yfinance", data), tm, RULES, TODAY)
    assert kept == ["GOODX"]
    assert conn.execute("SELECT ticker FROM young_listings").fetchall() == [("GOODX",)]


def test_screen_skips_a_listing_without_a_usable_cik(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(float("nan"), "NoCik", "NOCIK", "Nasdaq"), (10, "New", "NEWCO", "Nasdaq")])
    data = {t: bars(days(60), 50.0, volume=1_000_000.0) for t in ("NOCIK", "NEWCO")}
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    assert kept == ["NEWCO"]
    assert conn.execute("SELECT ticker, cik FROM young_listings").fetchall() == [("NEWCO", 10)]
    assert read_prices(conn, "NOCIK").empty


def test_screen_needs_enough_recent_bars(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "A", "FEWBARS", "Nasdaq"), (11, "B", "ENOUGH", "Nasdaq")])
    data = {"FEWBARS": bars(days(19), 500.0, volume=1_000_000.0),
            "ENOUGH": bars(days(20), 500.0, volume=1_000_000.0)}
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    assert kept == ["ENOUGH"]


def test_screen_liquidity_threshold_is_inclusive(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "A", "EXACT", "Nasdaq"), (11, "B", "BELOW", "Nasdaq")])
    data = {"EXACT": bars(days(60), 25.0, volume=1_000_000.0),   # $25M exactly
            "BELOW": bars(days(60), 25.0, volume=999_999.0)}
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    assert kept == ["EXACT"]


def test_screen_clipped_history_margin(tmp_path):
    conn = fresh_conn(tmp_path)
    start = date(2020, 1, 1)
    tm = ticker_map([(10, "A", "AT30", "Nasdaq"), (11, "B", "AT31", "Nasdaq")])
    data = {"AT30": bars(pd.bdate_range("2020-01-31", "2026-10-09"), 50.0, volume=1_000_000.0),
            "AT31": bars(pd.bdate_range("2020-02-03", "2026-10-09"), 50.0, volume=1_000_000.0)}
    # AT30's first bar is 2020-01-31 = start + 30 days: not "more than 30 days after" -> clipped.
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY,
                                 history_start=start)
    assert kept == ["AT31"]


def test_rescreen_replaces_prices_and_refreshes_the_row(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    data = {"NEWCO": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0)}
    screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES,
                          date(2026, 10, 2))
    screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    assert conn.execute("SELECT ticker, cik, first_trade_date, screened_on "
                        "FROM young_listings").fetchall() == [
        ("NEWCO", 10, "2023-03-01", "2026-10-09")]


# --- fix round 1: index tickers are screened too; window and staleness -------------------------

def test_screen_records_an_index_ticker_that_ipod_recently_without_downloading_its_history(
        tmp_path):
    conn = fresh_conn(tmp_path)
    full = bars(pd.bdate_range("2021-06-01", "2026-10-09"), 50.0, volume=1_000_000.0)
    replace_prices(conn, "IPOCO", full, "yfinance")    # already stored by the price sync
    tm = ticker_map([(10, "Ipo", "IPOCO", "Nasdaq")])
    provider = FakeProvider("yfinance", {"IPOCO": full.assign(close=51.0)})
    kept = screen_young_listings(conn, provider, tm, RULES, TODAY)
    assert kept == ["IPOCO"]
    assert conn.execute("SELECT ticker, cik, first_trade_date, screened_on "
                        "FROM young_listings").fetchall() == [
        ("IPOCO", 10, "2021-06-01", "2026-10-09")]
    assert len(provider.calls) == 1                     # only the short liquidity window
    assert provider.calls[0][1] == date(2026, 8, 25)
    assert set(read_prices(conn, "IPOCO")["close"]) == {50.0}  # stored bars are not rewritten


def test_screen_downloads_history_only_for_candidates_with_no_stored_prices(tmp_path):
    conn = fresh_conn(tmp_path)
    stored = bars(pd.bdate_range("2022-01-03", "2026-10-09"), 50.0, volume=1_000_000.0)
    replace_prices(conn, "HAVE", stored, "yfinance")
    data = {"HAVE": stored,
            "NEW": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0)}
    tm = ticker_map([(10, "Have", "HAVE", "NYSE"), (11, "New", "NEW", "Nasdaq")])
    provider = FakeProvider("yfinance", data)
    assert screen_young_listings(conn, provider, tm, RULES, TODAY) == ["HAVE", "NEW"]
    assert [c[0] for c in provider.calls] == [("HAVE", "NEW"), ("NEW",)]
    assert conn.execute("SELECT ticker, first_trade_date FROM young_listings "
                        "ORDER BY ticker").fetchall() == [("HAVE", "2022-01-03"),
                                                          ("NEW", "2023-03-01")]
    assert read_prices(conn, "NEW")["date"].min() == pd.Timestamp("2023-03-01")


def test_screen_applies_the_clipped_history_rule_to_stored_prices_too(tmp_path):
    conn = fresh_conn(tmp_path)
    start = date(2020, 1, 1)
    data = {"AT30": bars(pd.bdate_range("2020-01-31", "2026-10-09"), 50.0, volume=1_000_000.0),
            "AT31": bars(pd.bdate_range("2020-02-03", "2026-10-09"), 50.0, volume=1_000_000.0)}
    for t, f in data.items():
        replace_prices(conn, t, f, "yfinance")
    tm = ticker_map([(10, "A", "AT30", "Nasdaq"), (11, "B", "AT31", "Nasdaq")])
    provider = FakeProvider("yfinance", data)
    kept = screen_young_listings(conn, provider, tm, RULES, TODAY, history_start=start)
    assert kept == ["AT31"]
    assert len(provider.calls) == 1


def test_screen_window_follows_the_liquidity_rule(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    provider = FakeProvider("yfinance", {})
    screen_young_listings(conn, provider, tm, RULES, TODAY)
    assert provider.calls[0][1] == date(2026, 10, 9) - pd.Timedelta(days=45).to_pytimedelta()
    long_rules = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=60, young_max_years=5,
                               young_min_market_cap=2e9)
    provider = FakeProvider("yfinance", {})
    screen_young_listings(conn, provider, tm, long_rules, TODAY)
    # 60 trading days need ceil(60 * 7 / 5) + 15 = 99 calendar days
    assert provider.calls[0][1] == date(2026, 10, 9) - pd.Timedelta(days=99).to_pytimedelta()


def test_screen_rejects_a_candidate_whose_last_bar_is_stale(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "A", "STALE", "Nasdaq"), (11, "B", "LONGWE", "Nasdaq"),
                     (12, "C", "EDGE", "Nasdaq"), (13, "D", "OVER", "Nasdaq")])
    first = "2023-03-01"
    data = {
        "STALE": bars(pd.bdate_range(first, "2026-09-29"), 50.0, volume=1_000_000.0),   # 10 days
        "LONGWE": bars(pd.bdate_range(first, "2026-10-06"), 50.0, volume=1_000_000.0),  # 3 days
        "EDGE": bars(pd.bdate_range(first, "2026-10-02"), 50.0, volume=1_000_000.0),    # 7 days
        "OVER": bars(pd.bdate_range(first, "2026-10-01"), 50.0, volume=1_000_000.0),    # 8 days
    }
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    assert kept == ["EDGE", "LONGWE"]


# --- final review C1: the screen's full download records split events -------------------------

def test_screen_full_download_stores_the_listing_splits(tmp_path):
    from analyzer.data.price_sync import read_splits

    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    data = {"NEWCO": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0)}
    provider = FakeProvider("yfinance", data, splits={"NEWCO": [(date(2025, 6, 2), 10.0)]})
    assert screen_young_listings(conn, provider, tm, RULES, TODAY) == ["NEWCO"]
    assert read_splits(conn, "NEWCO") == [(date(2025, 6, 2), 10.0)]


# --- final review I1: only primary-provider history dates a listing ---------------------------

def _rows(conn):
    return conn.execute("SELECT ticker, cik, first_trade_date FROM young_listings "
                        "ORDER BY ticker").fetchall()


def _oldco_feeds():
    nasdaq_days = pd.bdate_range("2016-10-10", "2026-10-09")   # the fallback serves ~10 years
    nasdaq = FakeProvider("nasdaq", {"OLDCO": bars(nasdaq_days, 100.0, adj=float("nan"),
                                                   volume=1_000_000.0)})
    yf = FakeProvider("yfinance", {"OLDCO": bars(pd.bdate_range("2009-01-02", "2026-10-09"),
                                                 100.0, volume=1_000_000.0)})
    return yf, nasdaq


def test_fallback_built_history_never_makes_an_old_company_young(tmp_path):
    from analyzer.data.price_sync import sync_prices

    conn = fresh_conn(tmp_path)
    yf, nasdaq = _oldco_feeds()
    sync_prices(conn, [FakeProvider("yfinance", {}), nasdaq], ["OLDCO"], [], TODAY)
    tm = ticker_map([(123, "Old Co", "OLDCO", "NYSE")])
    assert screen_young_listings(conn, yf, tm, RULES, TODAY) == []
    assert _rows(conn) == []
    assert all(c[1] != date(2009, 1, 1) for c in yf.calls)  # stored: not downloaded by the screen
    # the price sync heals the history from the primary; the next screen judges it old
    sync_prices(conn, [yf, nasdaq], ["OLDCO"], [], TODAY + timedelta(days=1))
    assert read_prices(conn, "OLDCO")["date"].min() == pd.Timestamp("2009-01-02")
    assert screen_young_listings(conn, yf, tm, RULES, TODAY + timedelta(days=7)) == []
    assert _rows(conn) == []


def test_a_row_dated_from_fallback_history_is_deleted_once_the_history_is_healed(tmp_path):
    from analyzer.data.price_sync import sync_prices

    conn = fresh_conn(tmp_path)
    yf, nasdaq = _oldco_feeds()
    sync_prices(conn, [FakeProvider("yfinance", {}), nasdaq], ["OLDCO"], [], TODAY)
    conn.execute("INSERT INTO young_listings VALUES ('OLDCO', 123, '2016-10-10', '2026-10-09')")
    tm = ticker_map([(123, "Old Co", "OLDCO", "NYSE")])
    # still fallback-built: cannot be judged, so the row stays and the sync keeps tracking it
    screen_young_listings(conn, yf, tm, RULES, TODAY)
    assert _rows(conn) == [("OLDCO", 123, "2016-10-10")]
    sync_prices(conn, [yf, nasdaq], ["OLDCO"], [], TODAY + timedelta(days=1))
    screen_young_listings(conn, FakeProvider("yfinance", {}), tm, RULES, TODAY + timedelta(days=7))
    assert _rows(conn) == []


def test_older_fallback_rows_keep_a_stored_ticker_out_of_this_weeks_screen(tmp_path):
    conn = fresh_conn(tmp_path)
    replace_prices(conn, "MIXED", bars(pd.bdate_range("2016-01-04", "2026-09-30"), 50.0,
                                       adj=float("nan")), "nasdaq")
    write_prices(conn, "MIXED", bars(pd.bdate_range("2026-10-01", "2026-10-09"), 50.0),
                 "yfinance")
    tm = ticker_map([(10, "Mixed", "MIXED", "Nasdaq")])
    provider = FakeProvider("yfinance", {"MIXED": bars(days(60), 50.0, volume=1_000_000.0)})
    assert screen_young_listings(conn, provider, tm, RULES, TODAY) == []
    assert _rows(conn) == []
    assert len(provider.calls) == 1


def test_fallback_rows_after_the_first_primary_bar_do_not_matter(tmp_path):
    conn = fresh_conn(tmp_path)
    replace_prices(conn, "NEWCO", bars(pd.bdate_range("2023-03-01", "2026-10-06"), 50.0),
                   "yfinance")
    write_prices(conn, "NEWCO", bars(["2026-10-07", "2026-10-08", "2026-10-09"], 50.0,
                                     adj=float("nan")), "nasdaq")
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    provider = FakeProvider("yfinance", {"NEWCO": bars(days(60), 50.0, volume=1_000_000.0)})
    assert screen_young_listings(conn, provider, tm, RULES, TODAY) == ["NEWCO"]
    assert _rows(conn) == [("NEWCO", 10, "2023-03-01")]


def test_rescreen_redates_a_listing_whose_first_primary_bar_moved(tmp_path):
    conn = fresh_conn(tmp_path)
    replace_prices(conn, "NEWCO", bars(pd.bdate_range("2023-02-01", "2026-10-09"), 50.0),
                   "yfinance")   # a refetch brought earlier bars
    conn.execute("INSERT INTO young_listings VALUES ('NEWCO', 10, '2023-03-01', '2026-10-02')")
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    screen_young_listings(conn, FakeProvider("yfinance", {}), tm, RULES, TODAY)  # not liquid now
    assert conn.execute("SELECT ticker, cik, first_trade_date, screened_on "
                        "FROM young_listings").fetchall() == [
        ("NEWCO", 10, "2023-02-01", "2026-10-02")]


def test_a_correct_young_listing_keeps_its_row(tmp_path):
    conn = fresh_conn(tmp_path)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq")])
    data = {"NEWCO": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0)}
    screen_young_listings(conn, FakeProvider("yfinance", data), tm, RULES, TODAY)
    for later in (TODAY + timedelta(days=7), TODAY + timedelta(days=14)):
        screen_young_listings(conn, FakeProvider("yfinance", {}), tm, RULES, later)
        assert _rows(conn) == [("NEWCO", 10, "2023-03-01")]
    replace_prices(conn, "GONE", bars(pd.bdate_range("2022-01-03", "2023-01-03"), 5.0), "nasdaq")
    conn.execute("INSERT INTO young_listings VALUES ('GONE', 11, '2022-01-03', '2023-01-09')")
    screen_young_listings(conn, FakeProvider("yfinance", {}), tm, RULES, TODAY)
    assert _rows(conn)[0] == ("GONE", 11, "2022-01-03")   # no primary rows: left alone

