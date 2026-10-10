from datetime import date

import pandas as pd

from analyzer.data.prices import (
    PRICE_COLUMNS,
    NasdaqPrices,
    YFinancePrices,
    fetch_with_fallback,
)
from tests.helpers import FakeResponse, fake_client

FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _yf_frame():
    idx = pd.DatetimeIndex(["2026-10-08", "2026-10-09"], name="Date")
    cols = pd.MultiIndex.from_product([["AAPL", "DEAD"], FIELDS])
    nan = float("nan")
    rows = [
        [1.0, 2.0, 0.5, 1.5, 1.4, 100, nan, nan, nan, nan, nan, nan],
        [2.0, 3.0, 1.5, 2.5, 2.4, 200, nan, nan, nan, nan, nan, nan],
    ]
    return pd.DataFrame(rows, index=idx, columns=cols)


def test_yfinance_splits_batch_and_drops_missing_symbols():
    calls = []

    def download(symbols, start, end):
        calls.append(list(symbols))
        return _yf_frame()

    out = YFinancePrices(download=download).fetch(["AAPL", "DEAD"], date(2026, 10, 1), date(2026, 10, 9))
    assert list(out) == ["AAPL"]
    f = out["AAPL"]
    assert list(f.columns) == PRICE_COLUMNS
    assert f["date"].tolist() == [pd.Timestamp("2026-10-08"), pd.Timestamp("2026-10-09")]
    assert f["adj_close"].tolist() == [1.4, 2.4]
    assert calls == [["AAPL", "DEAD"]]


def test_yfinance_batches_symbols():
    calls = []

    def download(symbols, start, end):
        calls.append(list(symbols))
        return pd.DataFrame()

    YFinancePrices(download=download, batch_size=2).fetch(["A", "B", "C"], date(2026, 1, 1), date(2026, 1, 2))
    assert calls == [["A", "B"], ["C"]]


def test_yfinance_batch_error_is_skipped():
    def boom(*args):
        raise RuntimeError("rate limited")

    assert YFinancePrices(download=boom).fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2)) == {}


NASDAQ_ROWS = {
    "data": {
        "symbol": "BRK/B",
        "tradesTable": {
            "rows": [
                {"date": "10/09/2026", "close": "$1,515.62", "volume": "4,306,143",
                 "open": "$1,500.00", "high": "$1,520.10", "low": "$1,499.50"},
                {"date": "10/08/2026", "close": "$1,510.00", "volume": "N/A",
                 "open": "$1,505.00", "high": "$1,512.00", "low": "$1,501.00"},
            ]
        },
    }
}
NOT_FOUND = {"data": None, "status": {"rCode": 400}}


def test_nasdaq_parses_rows_and_maps_class_shares(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(json_data=NASDAQ_ROWS)])
    out = NasdaqPrices(client).fetch(["BRK-B"], date(2026, 10, 1), date(2026, 10, 9))
    f = out["BRK-B"]
    assert session.calls[0]["url"].endswith("/BRK.B/historical")
    assert session.calls[0]["params"]["assetclass"] == "stocks"
    assert f["date"].tolist() == [pd.Timestamp("2026-10-08"), pd.Timestamp("2026-10-09")]
    assert f["close"].tolist() == [1510.0, 1515.62]
    assert f["volume"].isna().tolist() == [True, False]
    assert f["adj_close"].isna().all()


def test_nasdaq_falls_back_to_etf_asset_class(tmp_path):
    client, session = fake_client(
        tmp_path, responses=[FakeResponse(json_data=NOT_FOUND), FakeResponse(json_data=NASDAQ_ROWS)]
    )
    out = NasdaqPrices(client).fetch(["SMH"], date(2026, 10, 1), date(2026, 10, 9))
    assert "SMH" in out
    assert [c["params"]["assetclass"] for c in session.calls] == ["stocks", "etf"]


def test_nasdaq_unknown_symbol_is_left_out(tmp_path):
    client, _ = fake_client(
        tmp_path, responses=[FakeResponse(json_data=NOT_FOUND), FakeResponse(json_data=NOT_FOUND)]
    )
    assert NasdaqPrices(client).fetch(["ZZZZQ"], date(2026, 10, 1), date(2026, 10, 9)) == {}


class StubProvider:
    def __init__(self, name, symbols):
        self.name = name
        self.symbols = set(symbols)
        self.asked = []

    def fetch(self, symbols, start, end):
        self.asked.append(list(symbols))
        frame = pd.DataFrame({c: [1.0] for c in PRICE_COLUMNS})
        frame["date"] = pd.Timestamp("2026-10-09")
        return {s: frame for s in symbols if s in self.symbols}


def test_fallback_chain_asks_next_provider_only_for_missing_symbols():
    a, b = StubProvider("a", ["X"]), StubProvider("b", ["Y"])
    res = fetch_with_fallback([a, b], ["X", "Y", "Z"], date(2026, 10, 1), date(2026, 10, 9))
    assert res.sources == {"X": "a", "Y": "b"}
    assert res.missing == ["Z"]
    assert b.asked == [["Y", "Z"]]
