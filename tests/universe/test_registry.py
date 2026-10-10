from datetime import date

import pandas as pd

from analyzer.store.db import connect, migrate
from analyzer.universe.registry import refresh_tickers


def test_refresh_tickers_combines_wikipedia_sec_and_sic(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO index_snapshots VALUES (?,?,?,?,?,?,?,?)",
        [("sp500", "2026-09-01", 1, "NVDA", "Nvidia", "Information Technology", "Old Sub", None),
         ("sp500", "2026-10-01", 2, "NVDA", "Nvidia", "Information Technology", "Semiconductors", None)],
    )
    conn.execute("INSERT INTO index_snapshots VALUES ('sp400','2026-10-01',3,'NOGX','NoGics',NULL,NULL,NULL)")
    conn.execute("INSERT INTO companies VALUES (555, 'NoGics Inc', '3674', 'Semis', 'x')")
    conn.execute("INSERT INTO young_listings VALUES ('SNDK', 2023207, '2025-02-24', '2026-10-10')")
    conn.execute("INSERT INTO companies VALUES (2023207, 'Sandisk Corp', '3572', 'Computer Storage', 'x')")
    ticker_map = pd.DataFrame(
        {"cik": [1045810, 2023207, 555], "name": ["NVIDIA CORP", "Sandisk Corp", "NoGics Inc"],
         "ticker": ["NVDA", "SNDK", "NOGX"], "exchange": ["Nasdaq", "Nasdaq", "NYSE"]})
    assert refresh_tickers(conn, ticker_map, date(2026, 10, 10)) == 3
    rows = dict((r[0], r[1:]) for r in conn.execute(
        "SELECT ticker, cik, gics_sector, sector_etf, industry_etf FROM tickers"))
    assert conn.execute(
        "SELECT gics_sub_industry FROM tickers WHERE ticker = 'NVDA'").fetchone()[0] == "Semiconductors"
    assert rows["NOGX"] == (555, None, "XLK", "SMH")
    assert rows["NVDA"] == (1045810, "Information Technology", "XLK", "SMH")
    assert rows["SNDK"] == (2023207, None, "XLK", None)


def test_young_listing_cik_fallback(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.execute("INSERT INTO young_listings VALUES ('NEWX', 777, '2026-01-02', '2026-10-10')")
    empty = pd.DataFrame({"cik": [], "name": [], "ticker": [], "exchange": []})
    assert refresh_tickers(conn, empty, date(2026, 10, 10)) == 1
    assert conn.execute("SELECT cik FROM tickers WHERE ticker = 'NEWX'").fetchone()[0] == 777
