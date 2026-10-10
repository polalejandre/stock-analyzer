import sqlite3
from datetime import date

import pytest

from analyzer.data.price_sync import (
    last_dates,
    latest_market_date,
    read_prices,
    replace_prices,
    sync_prices,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeProvider, bars

TODAY = date(2026, 10, 9)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_new_symbols_get_full_history_with_fallback(conn):
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-08", "2026-10-09"], [10, 11])})
    backup = FakeProvider("nasdaq", {"SMH": bars(["2026-10-09"], [600], adj=[float("nan")])})
    rep = sync_prices(conn, [primary, backup], ["AAPL", "SMH", "GONE"], ["OLD"], TODAY)
    assert sorted(rep.updated) == ["AAPL", "SMH"]
    assert rep.via_fallback == {"SMH": "nasdaq"}
    assert rep.missing == ["GONE"]
    assert rep.historical_missing == ["OLD"]
    smh = read_prices(conn, "SMH")
    assert smh["adj_close"].tolist() == [600.0]  # nothing stored before: adj = close
    assert smh["source"].tolist() == ["nasdaq"]


def test_incremental_appends_new_days(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-01", "2026-10-02", "2026-10-05"], [10, 11, 12])})
    rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert rep.updated == ["AAPL"] and rep.refetched == []
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11, 12]
    assert primary.calls[0][1] == date(2026, 9, 18)  # last date − 14 days


def test_dividend_adjustment_triggers_full_refetch(conn):
    replace_prices(conn, "KO", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [60, 61, 62]), "yfinance")
    new_history = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [60, 61, 62, 63],
                       adj=[59.4, 60.4, 61.4, 63])
    primary = FakeProvider("yfinance", {"KO": new_history})
    rep = sync_prices(conn, [primary], ["KO"], [], TODAY)
    assert rep.refetched == ["KO"]
    assert read_prices(conn, "KO")["adj_close"].tolist() == [59.4, 60.4, 61.4, 63]


def test_partial_last_bar_does_not_trigger_refetch(conn):
    # Stored 10-02 bar was captured intraday at 11.0; the final close is 11.5.
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-01", "2026-10-02"], [10, 11.5])})
    rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert rep.refetched == []
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11.5]


def test_partial_last_bars_never_cause_a_mass_refetch(conn):
    symbols = [f"S{i}" for i in range(5)]
    for sym in symbols:
        replace_prices(conn, sym, bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider(
        "yfinance", {sym: bars(["2026-10-01", "2026-10-02", "2026-10-05"], [10, 11.4, 12]) for sym in symbols}
    )
    rep = sync_prices(conn, [primary], symbols, [], TODAY)
    assert rep.refetched == [] and sorted(rep.updated) == symbols
    assert len(primary.calls) == 1  # one incremental download, no full-history refetch


def test_fallback_rows_reuse_latest_adjustment_ratio(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01"], [100], adj=[99]), "yfinance")
    primary = FakeProvider("yfinance", {})
    backup = FakeProvider("nasdaq", {"AAPL": bars(["2026-10-02"], [110], adj=[float("nan")])})
    rep = sync_prices(conn, [primary, backup], ["AAPL"], [], TODAY)
    assert rep.via_fallback == {"AAPL": "nasdaq"}
    assert read_prices(conn, "AAPL")["adj_close"].round(4).tolist() == [99.0, 108.9]


def test_historical_symbols_are_not_refreshed(conn):
    replace_prices(conn, "OLD", bars(["2015-01-02"], [5]), "yfinance")
    primary = FakeProvider("yfinance", {"OLD": bars(["2015-01-02", "2026-10-01"], [5, 6])})
    sync_prices(conn, [primary], [], ["OLD"], TODAY)
    assert primary.calls == []


def test_last_and_market_dates(conn):
    replace_prices(conn, "SPY", bars(["2026-10-08", "2026-10-09"], [600, 601]), "yfinance")
    assert last_dates(conn, ["SPY", "X"]) == {"SPY": date(2026, 10, 9)}
    assert latest_market_date(conn) == date(2026, 10, 9)


class ScriptedProvider:
    """Answers each fetch() call from the next entry of `script` (None -> no data)."""

    def __init__(self, name, script):
        self.name = name
        self.script = list(script)
        self.calls = []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        data = self.script.pop(0) if self.script else None
        return {s: f for s, f in (data or {}).items() if s in symbols}


def test_read_prices_is_typed_for_empty_null_and_normal_results(conn):
    empty = read_prices(conn, "NOPE")
    assert empty.empty
    assert list(empty.columns) == ["date", "open", "high", "low", "close", "adj_close", "volume", "source"]
    assert str(empty["date"].dtype) == "datetime64[ns]"

    frame = bars(["2026-10-01", "2026-10-02"], [10.0, 11.0])
    frame["open"] = float("nan")  # a column that is entirely missing must still be float64
    frame["volume"] = float("nan")
    replace_prices(conn, "AAPL", frame, "yfinance")
    got = read_prices(conn, "AAPL")
    assert str(got["date"].dtype) == "datetime64[ns]"  # same unit providers emit
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        assert str(got[col].dtype) == "float64", col
    assert got["open"].isna().all() and got["volume"].isna().all()
    # date filters are inclusive on both ends
    both = read_prices(conn, "AAPL", start=date(2026, 10, 2), end=date(2026, 10, 2))
    assert both["close"].tolist() == [11.0]


def test_replace_prices_is_atomic(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    with pytest.raises(sqlite3.IntegrityError):  # source is NOT NULL: fails after the delete ran
        replace_prices(conn, "AAPL", bars(["2026-10-05"], [12]), None)
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11]


def test_split_triggers_full_refetch(conn):
    replace_prices(conn, "NVDA", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [100, 100, 100]), "yfinance")
    halved = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [50, 50, 50, 51])
    primary = FakeProvider("yfinance", {"NVDA": halved})
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.refetched == ["NVDA"] and rep.updated == []
    assert read_prices(conn, "NVDA")["close"].tolist() == [50, 50, 50, 51]
    # the refetch asks the primary for the whole history
    assert primary.calls[-1][1] == date(2009, 1, 1)


def test_refetch_that_returns_nothing_keeps_stored_rows_and_reports_missing(conn):
    replace_prices(conn, "KO", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [60, 61, 62]), "yfinance")
    changed = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [60, 61, 62, 63],
                   adj=[59.4, 60.4, 61.4, 63])
    primary = ScriptedProvider("yfinance", [{"KO": changed}, None])
    rep = sync_prices(conn, [primary], ["KO"], [], TODAY)
    assert rep.missing == ["KO"] and rep.refetched == [] and rep.updated == []
    # nothing half-written: neither the new day nor re-adjusted values were stored
    assert read_prices(conn, "KO")["adj_close"].tolist() == [60, 61, 62]


def test_unchanged_history_is_not_refetched_on_repeat_runs(conn):
    data = bars(["2026-10-01", "2026-10-02", "2026-10-05"], [10, 11, 12], adj=[9.5, 10.5, 12])
    primary = FakeProvider("yfinance", {"AAPL": data})
    first = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert first.updated == ["AAPL"]
    for _ in range(2):
        rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
        assert rep.updated == ["AAPL"] and rep.refetched == [] and rep.missing == []
    assert len(primary.calls) == 3  # one full download, then two small incremental ones
    assert read_prices(conn, "AAPL")["adj_close"].tolist() == [9.5, 10.5, 12]


def test_missing_adj_close_in_stored_rows_does_not_hide_a_close_change(conn):
    stored = bars(["2026-09-30", "2026-10-01", "2026-10-02"], [100, 100, 100])
    stored["adj_close"] = float("nan")
    replace_prices(conn, "NVDA", stored, "yfinance")
    primary = FakeProvider("yfinance", {"NVDA": bars(["2026-09-30", "2026-10-01", "2026-10-02"], [50, 50, 50])})
    assert sync_prices(conn, [primary], ["NVDA"], [], TODAY).refetched == ["NVDA"]


def test_fallback_does_not_rewrite_stored_primary_history(conn):
    # A dividend went ex on 10-01, so adj/close steps from 0.98 to 0.99.
    replace_prices(
        conn, "KO",
        bars(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"], [100] * 5,
             adj=[98, 98, 98, 99, 99]),
        "yfinance",
    )
    primary = FakeProvider("yfinance", {})
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"KO": bars(
        ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"],
        [100, 100, 100, 100, 100, 101], adj=[nan] * 6)})
    rep = sync_prices(conn, [primary, backup], ["KO"], [], TODAY)
    assert rep.via_fallback == {"KO": "nasdaq"}
    got = read_prices(conn, "KO")
    # history before the last stored bar is untouched: values and source
    assert got["adj_close"].round(4).tolist() == [98, 98, 98, 99, 99, 99.99]
    assert got["source"].tolist() == ["yfinance"] * 4 + ["nasdaq"] * 2


def test_adjustment_change_is_still_detected_after_a_fallback_day(conn):
    replace_prices(
        conn, "KO",
        bars(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"], [100] * 4, adj=[100] * 4),
        "yfinance",
    )
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"KO": bars(
        ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"],
        [100, 100, 100, 100, 101], adj=[nan] * 5)})
    sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["KO"], [], TODAY)
    # next day the primary is back, and a dividend has re-adjusted the whole history by 1%
    primary = FakeProvider("yfinance", {"KO": bars(
        ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"],
        [100, 100, 100, 100, 101, 102], adj=[99, 99, 99, 99, 100, 102])})
    rep = sync_prices(conn, [primary], ["KO"], [], TODAY)
    assert rep.refetched == ["KO"]
    assert read_prices(conn, "KO")["adj_close"].tolist() == [99, 99, 99, 99, 100, 102]
    assert set(read_prices(conn, "KO")["source"]) == {"yfinance"}


def test_stale_symbol_is_redownloaded_in_full_and_ignores_old_adjustment_ratio(conn):
    replace_prices(conn, "OLD", bars(["2024-01-02"], [50], adj=[25]), "yfinance")  # last bar > 365 days ago
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"OLD": bars(["2026-10-01", "2026-10-02"], [60, 61], adj=[nan, nan])})
    rep = sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["OLD"], [], TODAY,
                      history_start=date(2020, 1, 1))
    assert rep.updated == ["OLD"] and rep.via_fallback == {"OLD": "nasdaq"}
    assert backup.calls[0][1] == date(2020, 1, 1)
    got = read_prices(conn, "OLD")
    assert got["close"].tolist() == [60, 61]  # the old row is gone
    assert got["adj_close"].tolist() == [60, 61]  # and its 0.5 ratio is not carried over


def test_duplicate_dates_from_a_provider_do_not_abort_the_sync(conn):
    twice = bars(["2026-10-01", "2026-10-02", "2026-10-02"], [10, 11, 11.5])
    primary = FakeProvider("yfinance", {"AAPL": twice, "MSFT": bars(["2026-10-02"], [20])})
    rep = sync_prices(conn, [primary], ["AAPL", "MSFT"], [], TODAY)
    assert sorted(rep.updated) == ["AAPL", "MSFT"]
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11.5]  # last one wins


def test_repeated_symbols_are_synced_once(conn):
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-02"], [11])})
    rep = sync_prices(conn, [primary], ["AAPL", "AAPL"], ["AAPL"], TODAY)
    assert rep.updated == ["AAPL"]
    assert primary.calls[0][0] == ("AAPL",)


def test_nan_close_rows_are_skipped_not_fatal(conn):
    frame = bars(["2026-10-01", "2026-10-02"], [10, 11])
    frame.loc[0, "close"] = float("nan")
    primary = FakeProvider("yfinance", {"AAPL": frame})
    rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert rep.updated == ["AAPL"]
    assert read_prices(conn, "AAPL")["close"].tolist() == [11]


def test_frame_with_no_usable_rows_never_wipes_stored_history(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    frame = bars(["2026-10-01", "2026-10-02"], [10, 11])
    frame["close"] = float("nan")
    changed = ScriptedProvider("yfinance", [{"AAPL": bars(["2026-10-01", "2026-10-02"], [5, 5.5])}, {"AAPL": frame}])
    rep = sync_prices(conn, [changed], ["AAPL"], [], TODAY)  # split -> refetch -> only NaN closes back
    assert rep.refetched == [] and rep.missing == ["AAPL"]
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11]
