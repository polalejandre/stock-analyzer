from datetime import date

import pandas as pd

from analyzer.store.db import connect, migrate
from analyzer.universe.registry import refresh_tickers


def test_refresh_tickers_combines_wikipedia_sec_and_sic(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO index_snapshots VALUES (?,?,?,?,?,?,?,?)",
        [("sp500", "2026-09-01", 1, "NVDA", "Nvidia", "Information Technology", "Semiconductors", None),
         ("sp500", "2026-10-01", 2, "NVDA", "Nvidia", "Information Technology", "Semiconductors", None)],
    )
    conn.execute("INSERT INTO young_listings VALUES ('SNDK', 2023207, '2025-02-24', '2026-10-10')")
    conn.execute("INSERT INTO companies VALUES (2023207, 'Sandisk Corp', '3572', 'Computer Storage', 'x')")
    ticker_map = pd.DataFrame(
        {"cik": [1045810, 2023207], "name": ["NVIDIA CORP", "Sandisk Corp"],
         "ticker": ["NVDA", "SNDK"], "exchange": ["Nasdaq", "Nasdaq"]})
    assert refresh_tickers(conn, ticker_map, date(2026, 10, 10)) == 2
    rows = dict((r[0], r[1:]) for r in conn.execute(
        "SELECT ticker, cik, gics_sector, sector_etf, industry_etf FROM tickers"))
    assert rows["NVDA"] == (1045810, "Information Technology", "XLK", "SMH")
    assert rows["SNDK"] == (2023207, None, "XLK", None)
