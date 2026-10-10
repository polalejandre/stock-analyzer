import json
import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

from analyzer.data.price_sync import (
    last_dates,
    latest_market_date,
    read_prices,
    read_splits,
    replace_prices,
    replace_splits,
    sync_prices,
)
from analyzer.store.db import connect, get_state, migrate
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
    backup = FakeProvider("nasdaq", {"OLD": bars(["2024-01-02", "2026-10-01", "2026-10-02"], [50, 60, 61], adj=[nan] * 3)})
    rep = sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["OLD"], [], TODAY,
                      history_start=date(2020, 1, 1))
    assert rep.updated == ["OLD"] and rep.via_fallback == {"OLD": "nasdaq"}
    assert backup.calls[0][1] == date(2020, 1, 1)
    got = read_prices(conn, "OLD")
    assert got["close"].tolist() == [50, 60, 61]
    assert got["adj_close"].tolist() == [50, 60, 61]  # the old row's 0.5 ratio is not carried over
    assert set(got["source"]) == {"nasdaq"}


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


# ---- fix round 1 ----

def _daily(n, start="2026-09-01", close=100.0):
    idx = pd.bdate_range(start, periods=n)
    return bars(list(idx.strftime("%Y-%m-%d")), [close] * n)


def test_truncated_primary_response_never_replaces_long_history(conn):
    stored = _daily(24)  # 2026-09-01 .. 2026-10-02
    replace_prices(conn, "NVDA", stored, "yfinance")
    window = bars(["2026-09-18", "2026-09-21", "2026-09-22", "2026-10-01", "2026-10-02", "2026-10-05"],
                  [50] * 6)  # a split: closes halved, so the incremental check asks for a refetch
    truncated = bars(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"], [50] * 4)
    primary = ScriptedProvider("yfinance", [{"NVDA": window}, {"NVDA": truncated}])
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.missing == ["NVDA"] and rep.refetched == [] and rep.updated == []
    got = read_prices(conn, "NVDA")
    assert len(got) == 24 and got["close"].tolist() == [100.0] * 24


def test_replacement_that_starts_late_is_refused_even_when_long_enough(conn):
    replace_prices(conn, "NVDA", _daily(24), "yfinance")  # first bar 2026-09-01
    late = _daily(30, start="2026-09-14", close=50.0)  # more rows, but starts 13 days after the stored start
    primary = ScriptedProvider("yfinance", [{"NVDA": late}, {"NVDA": late}])
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.missing == ["NVDA"] and rep.refetched == []
    assert read_prices(conn, "NVDA")["close"].tolist() == [100.0] * 24


def test_replace_prices_refuses_a_frame_without_usable_rows(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    with pytest.raises(ValueError):
        replace_prices(conn, "AAPL", bars([], []), "yfinance")
    nan_only = bars(["2026-10-05"], [12])
    nan_only["close"] = float("nan")
    with pytest.raises(ValueError):
        replace_prices(conn, "AAPL", nan_only, "yfinance")
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11]


def test_fallback_overlap_that_disagrees_with_stored_closes_is_not_written(conn):
    replace_prices(conn, "KO", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [100, 100, 100]), "yfinance")
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"KO": bars(
        ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [50, 50, 50, 51], adj=[nan] * 4)})
    rep = sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["KO"], [], TODAY)  # split during an outage
    assert rep.missing == ["KO"] and rep.updated == [] and rep.via_fallback == {}
    got = read_prices(conn, "KO")
    assert got["close"].tolist() == [100, 100, 100] and set(got["source"]) == {"yfinance"}


def test_fallback_needs_a_newer_bar_than_the_last_stored_one(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")  # 10-02 maybe partial
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"AAPL": bars(["2026-10-01", "2026-10-02"], [10, 11.5], adj=[nan, nan])})
    rep = sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["AAPL"], [], TODAY)
    assert rep.updated == [] and rep.via_fallback == {} and rep.missing == []
    got = read_prices(conn, "AAPL")
    assert got["close"].tolist() == [10, 11] and set(got["source"]) == {"yfinance"}


def test_fallback_refreshes_the_last_bar_when_it_adds_a_newer_one(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"AAPL": bars(
        ["2026-10-01", "2026-10-02", "2026-10-05"], [10, 11.5, 12], adj=[nan] * 3)})
    rep = sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["AAPL"], [], TODAY)
    assert rep.updated == ["AAPL"] and rep.via_fallback == {"AAPL": "nasdaq"}
    got = read_prices(conn, "AAPL")
    assert got["close"].tolist() == [10, 11.5, 12]
    assert got["source"].tolist() == ["yfinance", "nasdaq", "nasdaq"]


def test_microsecond_dates_from_a_primary_still_match_stored_rows(conn):
    replace_prices(conn, "KO", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [60, 61, 62]), "yfinance")
    fresh = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [60, 61, 62, 63],
                 adj=[59.4, 60.4, 61.4, 63])
    fresh["date"] = fresh["date"].astype("datetime64[us]")
    assert str(fresh["date"].dtype) == "datetime64[us]"
    rep = sync_prices(conn, [FakeProvider("yfinance", {"KO": fresh})], ["KO"], [], TODAY)
    assert rep.refetched == ["KO"]
    assert read_prices(conn, "KO")["adj_close"].tolist() == [59.4, 60.4, 61.4, 63]


def test_fallback_built_history_is_replaced_once_the_primary_returns(conn):
    dates = list(pd.bdate_range("2026-09-01", periods=24).strftime("%Y-%m-%d"))  # ends 2026-10-02
    replace_prices(conn, "SMH", bars(dates, [100.0] * 24), "nasdaq")  # adj = close, no dividend adjustment
    real = bars([*dates, "2026-10-05"], [100.0] * 25, adj=[98.0] * 24 + [100.0])
    primary = FakeProvider("yfinance", {"SMH": real})
    rep = sync_prices(conn, [primary], ["SMH"], [], TODAY)
    assert rep.refetched == ["SMH"] and rep.updated == [] and rep.via_fallback == {}
    got = read_prices(conn, "SMH")
    assert len(got) == 25 and set(got["source"]) == {"yfinance"}
    assert got["adj_close"].tolist() == [98.0] * 24 + [100.0]
    assert primary.calls[-1][1] == date(2009, 1, 1)  # a full-history request
    # healed: the next run is an ordinary incremental one
    again = sync_prices(conn, [primary], ["SMH"], [], TODAY)
    assert again.updated == ["SMH"] and again.refetched == []


def test_fallback_rows_inside_the_window_do_not_force_a_heal(conn):
    replace_prices(conn, "KO", bars(["2026-09-29", "2026-09-30", "2026-10-01"], [100] * 3), "yfinance")
    nan = float("nan")
    backup = FakeProvider("nasdaq", {"KO": bars(["2026-10-01", "2026-10-02"], [100, 101], adj=[nan, nan])})
    sync_prices(conn, [FakeProvider("yfinance", {}), backup], ["KO"], [], TODAY)
    primary = FakeProvider("yfinance", {"KO": bars(
        ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [100, 100, 100, 101, 102])})
    rep = sync_prices(conn, [primary], ["KO"], [], TODAY)
    assert rep.refetched == [] and rep.updated == ["KO"]
    assert set(read_prices(conn, "KO")["source"]) == {"yfinance"}


def test_one_stuck_symbol_does_not_widen_the_window_for_the_others(conn):
    current = ["2026-10-07", "2026-10-08"]
    for sym in ("AAA", "BBB"):
        replace_prices(conn, sym, bars(current, [10, 11]), "yfinance")
    replace_prices(conn, "STUCK", bars(["2026-03-23"], [5]), "yfinance")  # 200 days old, not yet stale
    data = {sym: bars([*current, "2026-10-09"], [10, 11, 12]) for sym in ("AAA", "BBB")}
    data["STUCK"] = bars(["2026-03-23"], [5])
    primary = FakeProvider("yfinance", data)
    rep = sync_prices(conn, [primary], ["AAA", "BBB", "STUCK"], [], TODAY)
    assert sorted(rep.updated) == ["AAA", "BBB", "STUCK"]
    by_start = {start: symbols for symbols, start, _ in primary.calls}
    assert by_start == {
        date(2026, 9, 24): ("AAA", "BBB"),  # last 10-08 - 14 days
        date(2026, 3, 9): ("STUCK",),  # last 03-23 - 14 days
    }


def test_missed_historical_symbols_are_not_requested_again_for_30_days(conn):
    primary = FakeProvider("yfinance", {})
    first = sync_prices(conn, [primary], ["AAPL"], ["OLD"], TODAY)
    assert first.historical_missing == ["OLD"] and first.missing == ["AAPL"]
    assert json.loads(get_state(conn, "price_historical_misses")) == {"OLD": "2026-10-09"}
    assert primary.calls == [(("AAPL", "OLD"), date(2009, 1, 1), TODAY)]

    later = TODAY + timedelta(days=29)
    second = sync_prices(conn, [primary], ["AAPL"], ["OLD"], later)
    assert second.historical_missing == ["OLD"]  # still unknown, just not requested
    assert second.missing == ["AAPL"]  # active symbols are never skipped
    assert primary.calls[1] == (("AAPL",), date(2009, 1, 1), later)

    retry_day = TODAY + timedelta(days=30)
    primary.data["OLD"] = bars(["2015-01-02"], [5])
    third = sync_prices(conn, [primary], [], ["OLD"], retry_day)
    assert third.updated == ["OLD"] and third.historical_missing == []
    assert primary.calls[2][0] == ("OLD",)
    assert json.loads(get_state(conn, "price_historical_misses")) == {}


def test_a_still_missing_historical_symbol_restarts_its_30_day_clock(conn):
    primary = FakeProvider("yfinance", {})
    sync_prices(conn, [primary], [], ["OLD"], TODAY)
    retry_day = TODAY + timedelta(days=30)
    sync_prices(conn, [primary], [], ["OLD"], retry_day)
    assert len(primary.calls) == 2
    sync_prices(conn, [primary], [], ["OLD"], retry_day + timedelta(days=1))
    assert len(primary.calls) == 2
    assert json.loads(get_state(conn, "price_historical_misses")) == {"OLD": retry_day.isoformat()}


def test_negative_cache_ignores_malformed_state_and_other_symbols(conn):
    from analyzer.store.db import set_state

    set_state(conn, "price_historical_misses", "not json")
    primary = FakeProvider("yfinance", {"OLD": bars(["2015-01-02"], [5])})
    rep = sync_prices(conn, [primary], [], ["OLD"], TODAY)
    assert rep.updated == ["OLD"]
    set_state(conn, "price_historical_misses", json.dumps({"OTHER": "2026-10-01", "BAD": "garbage"}))
    rep = sync_prices(conn, [primary], [], ["NEW"], TODAY)
    assert rep.historical_missing == ["NEW"]
    assert json.loads(get_state(conn, "price_historical_misses")) == {"OTHER": "2026-10-01", "NEW": "2026-10-09"}


# ---- fix round 2: a refused heal must not degrade the normal path ----

HEAL_KEY = "price_heal_refused"


def _nasdaq_history(conn, n=24):
    dates = list(pd.bdate_range("2026-09-01", periods=n).strftime("%Y-%m-%d"))  # 24 -> ends 2026-10-02
    replace_prices(conn, "SMH", bars(dates, [100.0] * n), "nasdaq")
    return dates


def _short_primary():
    """A primary whose history starts 2026-09-21: too short to replace the stored 2026-09-01 start."""
    dates = list(pd.bdate_range("2026-09-21", periods=12).strftime("%Y-%m-%d"))  # .. 2026-10-06
    return FakeProvider("yfinance", {"SMH": bars(dates, [101.0] * 12)})


def _full_requests(primary):
    return [c for c in primary.calls if c[1] == date(2009, 1, 1)]


def test_refused_heal_still_writes_the_incremental_bars_and_is_not_missing(conn):
    _nasdaq_history(conn)
    primary = _short_primary()
    rep = sync_prices(conn, [primary], ["SMH"], [], TODAY)
    assert rep.missing == [] and rep.refetched == []
    assert rep.updated == ["SMH"] and rep.via_fallback == {}
    got = read_prices(conn, "SMH").set_index(read_prices(conn, "SMH")["date"].dt.strftime("%Y-%m-%d"))
    assert len(got) == 24 + 2  # 2026-10-05 and 10-06 are new; the older rows stay
    assert got.loc["2026-10-06", "close"] == 101.0 and got.loc["2026-10-06", "source"] == "yfinance"
    assert got.loc["2026-09-01", "source"] == "nasdaq"  # the old history was not touched
    assert len(_full_requests(primary)) == 1  # the heal was tried once
    assert json.loads(get_state(conn, HEAL_KEY)) == {"SMH": "2026-10-09"}


def test_refused_heal_is_not_retried_for_30_days(conn):
    _nasdaq_history(conn)
    primary = _short_primary()
    sync_prices(conn, [primary], ["SMH"], [], TODAY)
    for days in (1, 29):
        rep = sync_prices(conn, [primary], ["SMH"], [], TODAY + timedelta(days=days))
        assert rep.missing == [] and rep.refetched == [] and rep.updated == ["SMH"]
    assert len(_full_requests(primary)) == 1
    assert json.loads(get_state(conn, HEAL_KEY)) == {"SMH": "2026-10-09"}


def test_refused_heal_is_retried_after_30_days_and_refusal_restarts_the_clock(conn):
    _nasdaq_history(conn)
    primary = _short_primary()
    sync_prices(conn, [primary], ["SMH"], [], TODAY)
    retry_day = TODAY + timedelta(days=30)
    rep = sync_prices(conn, [primary], ["SMH"], [], retry_day)
    assert len(_full_requests(primary)) == 2 and rep.missing == [] and rep.refetched == []
    assert json.loads(get_state(conn, HEAL_KEY)) == {"SMH": retry_day.isoformat()}
    sync_prices(conn, [primary], ["SMH"], [], retry_day + timedelta(days=1))
    assert len(_full_requests(primary)) == 2


def test_heal_succeeds_on_retry_and_clears_the_refusal(conn):
    dates = _nasdaq_history(conn)
    primary = _short_primary()
    sync_prices(conn, [primary], ["SMH"], [], TODAY)
    primary.data["SMH"] = bars([*dates, "2026-10-05", "2026-10-06"], [101.0] * 26)  # now a full history
    retry_day = TODAY + timedelta(days=30)
    rep = sync_prices(conn, [primary], ["SMH"], [], retry_day)
    assert rep.refetched == ["SMH"] and rep.missing == []
    got = read_prices(conn, "SMH")
    assert len(got) == 26 and set(got["source"]) == {"yfinance"}
    assert json.loads(get_state(conn, HEAL_KEY)) == {}


def test_heal_refetch_that_returns_nothing_falls_through_and_is_retried_next_run(conn):
    _nasdaq_history(conn)
    window = bars(["2026-09-21", "2026-10-02", "2026-10-05"], [100.0, 100.0, 101.0])
    primary = ScriptedProvider("yfinance", [{"SMH": window}, None])  # the full request comes back empty
    rep = sync_prices(conn, [primary], ["SMH"], [], TODAY)
    assert rep.updated == ["SMH"] and rep.missing == [] and rep.refetched == []
    assert read_prices(conn, "SMH")["close"].tolist()[-1] == 101.0
    assert get_state(conn, HEAL_KEY) is None  # transient: no 30-day back-off


def test_adjustment_refetch_refusal_still_reports_missing(conn):
    # only a heal falls through; a changed adjustment with a refused refetch must not write new rows
    replace_prices(conn, "NVDA", _daily(24), "yfinance")
    window = bars(["2026-09-18", "2026-09-21", "2026-09-22", "2026-10-01", "2026-10-02", "2026-10-05"], [50] * 6)
    truncated = bars(["2026-10-01", "2026-10-02", "2026-10-05"], [50] * 3)
    primary = ScriptedProvider("yfinance", [{"NVDA": window}, {"NVDA": truncated}])
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.missing == ["NVDA"] and rep.updated == []
    assert get_state(conn, HEAL_KEY) is None


# --- final review C1: split events are stored with every full primary history ----------------

def test_full_primary_history_stores_its_splits_and_fallback_history_stores_none(conn):
    primary = FakeProvider("yfinance", {"NVDA": bars(["2024-06-07", "2024-06-10"], [120, 121]),
                                        "KO": bars(["2024-06-07"], [60])},
                           splits={"NVDA": [(date(2024, 6, 10), 10.0)]})
    backup = FakeProvider("nasdaq", {"SMH": bars(["2024-06-07"], [200], adj=[float("nan")])})
    replace_splits(conn, "SMH", [(date(2000, 1, 3), 2.0)])
    sync_prices(conn, [primary, backup], ["NVDA", "KO", "SMH"], [], TODAY)
    assert read_splits(conn, "NVDA") == [(date(2024, 6, 10), 10.0)]
    assert read_splits(conn, "KO") == []
    assert read_splits(conn, "SMH") == [(date(2000, 1, 3), 2.0)]  # Nasdaq reports no splits


def test_adjustment_change_refetch_records_the_new_split(conn):
    replace_prices(conn, "NVDA", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [100] * 3),
                   "yfinance")
    replace_splits(conn, "NVDA", [(date(2021, 7, 20), 4.0)])
    halved = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [50, 50, 50, 51])
    primary = FakeProvider("yfinance", {"NVDA": halved},
                           splits={"NVDA": [(date(2021, 7, 20), 4.0), (date(2026, 10, 5), 2.0)]})
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.refetched == ["NVDA"]
    assert read_splits(conn, "NVDA") == [(date(2021, 7, 20), 4.0), (date(2026, 10, 5), 2.0)]


def test_refused_adjustment_refetch_keeps_the_old_splits(conn):
    replace_prices(conn, "NVDA", bars(pd.bdate_range("2026-06-01", "2026-10-02"), 100.0),
                   "yfinance")
    replace_splits(conn, "NVDA", [(date(2021, 7, 20), 4.0)])
    halved = bars(pd.bdate_range("2026-09-14", "2026-10-05"), 50.0)  # too short to replace
    primary = FakeProvider("yfinance", {"NVDA": halved},
                           splits={"NVDA": [(date(2026, 10, 5), 2.0)]})
    rep = sync_prices(conn, [primary], ["NVDA"], [], TODAY)
    assert rep.missing == ["NVDA"]
    assert read_splits(conn, "NVDA") == [(date(2021, 7, 20), 4.0)]  # still the stored basis


def test_heal_records_the_primary_splits(conn):
    dates = list(pd.bdate_range("2026-09-01", periods=24).strftime("%Y-%m-%d"))
    replace_prices(conn, "SMH", bars(dates, [100.0] * 24), "nasdaq")
    real = bars([*dates, "2026-10-05"], [100.0] * 25, adj=[98.0] * 24 + [100.0])
    primary = FakeProvider("yfinance", {"SMH": real}, splits={"SMH": [(date(2026, 9, 2), 3.0)]})
    rep = sync_prices(conn, [primary], ["SMH"], [], TODAY)
    assert rep.refetched == ["SMH"]
    assert read_splits(conn, "SMH") == [(date(2026, 9, 2), 3.0)]


def test_stale_symbol_redownload_refreshes_its_splits(conn):
    replace_prices(conn, "OLD", bars(["2024-06-03", "2024-06-04"], [10, 10]), "yfinance")
    replace_splits(conn, "OLD", [(date(2024, 6, 4), 5.0)])
    primary = FakeProvider("yfinance", {"OLD": bars(["2024-06-03", "2024-06-04", "2026-10-09"],
                                                    [2, 2, 3])}, splits={})
    sync_prices(conn, [primary], ["OLD"], [], TODAY)
    assert read_splits(conn, "OLD") == []  # the new full history reports no split


def test_incremental_windows_do_not_touch_splits(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-01", "2026-10-02", "2026-10-05"],
                                                     [10, 11, 12])},
                           splits={"AAPL": [(date(2026, 10, 5), 1.0)]})
    sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert read_splits(conn, "AAPL") == []


# --- final review M13: an interrupted first run keeps the chunks it finished -------------------

def test_full_download_is_written_chunk_by_chunk(conn):
    from analyzer.data.price_sync import FULL_CHUNK_SIZE

    symbols = [f"S{i:03d}" for i in range(FULL_CHUNK_SIZE + 5)]
    data = {s: bars(["2026-10-08", "2026-10-09"], [10, 11]) for s in symbols}

    class InterruptedProvider(FakeProvider):
        def fetch(self, syms, start, end):
            if len(self.calls) == 1:  # the second chunk
                raise KeyboardInterrupt
            return super().fetch(syms, start, end)

    primary = InterruptedProvider("yfinance", data, splits={symbols[0]: [(date(2026, 10, 9), 2.0)]})
    with pytest.raises(KeyboardInterrupt):
        sync_prices(conn, [primary], symbols, [], TODAY)
    assert [len(c[0]) for c in primary.calls] == [FULL_CHUNK_SIZE]
    stored = set(last_dates(conn, symbols))
    assert stored == set(symbols[:FULL_CHUNK_SIZE])
    assert read_splits(conn, symbols[0]) == [(date(2026, 10, 9), 2.0)]
    # the rerun downloads only what is left
    primary = FakeProvider("yfinance", data)
    rep = sync_prices(conn, [primary], symbols, [], TODAY)
    assert primary.calls[0][0] == tuple(symbols[FULL_CHUNK_SIZE:])
    assert sorted(rep.updated) == symbols
    assert set(last_dates(conn, symbols)) == set(symbols)


def test_chunked_full_download_reports_fallback_and_missing_across_chunks(conn):
    from analyzer.data.price_sync import FULL_CHUNK_SIZE

    symbols = [f"S{i:03d}" for i in range(2 * FULL_CHUNK_SIZE + 1)]
    data = {s: bars(["2026-10-09"], [10]) for s in symbols[:-2]}
    primary = FakeProvider("yfinance", data)
    nan = float("nan")
    backup = FakeProvider("nasdaq", {symbols[-2]: bars(["2026-10-09"], [5], adj=[nan])})
    rep = sync_prices(conn, [primary, backup], symbols[:-1], [symbols[-1]], TODAY)
    assert [len(c[0]) for c in primary.calls] == [FULL_CHUNK_SIZE, FULL_CHUNK_SIZE, 1]
    assert rep.via_fallback == {symbols[-2]: "nasdaq"}
    assert rep.missing == []
    assert rep.historical_missing == [symbols[-1]]
    assert len(rep.updated) == len(symbols) - 1
