"""The `tickers` table: every stock the engine tracks, with CIK and reference ETFs."""

from __future__ import annotations

from datetime import date

import pandas as pd

from analyzer.store.db import upsert_rows
from analyzer.universe.industry import reference_etfs

COLUMNS = ["ticker", "cik", "name", "exchange", "gics_sector", "gics_sub_industry",
           "sector_etf", "industry_etf", "updated_at"]


def refresh_tickers(conn, ticker_map: pd.DataFrame, today: date) -> int:
    latest = pd.read_sql_query(
        "SELECT s.ticker, s.security, s.gics_sector, s.gics_sub_industry FROM index_snapshots s "
        "JOIN (SELECT ticker, MAX(snapshot_date) AS d FROM index_snapshots GROUP BY ticker) m "
        "ON m.ticker = s.ticker AND m.d = s.snapshot_date",
        conn,
    ).drop_duplicates("ticker").set_index("ticker")
    young = {r[0] for r in conn.execute("SELECT ticker FROM young_listings")}
    sec = ticker_map.set_index("ticker")
    sic = dict(conn.execute("SELECT cik, sic FROM companies").fetchall())

    rows = []
    for ticker in sorted(set(latest.index) | young):
        cik = int(sec.at[ticker, "cik"]) if ticker in sec.index else None
        name = sec.at[ticker, "name"] if ticker in sec.index else None
        exchange = sec.at[ticker, "exchange"] if ticker in sec.index else None
        sector = latest.at[ticker, "gics_sector"] if ticker in latest.index else None
        sub = latest.at[ticker, "gics_sub_industry"] if ticker in latest.index else None
        if name is None and ticker in latest.index:
            name = latest.at[ticker, "security"]
        ref = reference_etfs(sector, sub, sic.get(cik))
        rows.append((ticker, cik, name, exchange, sector, sub, ref.sector_etf, ref.industry_etf,
                     today.isoformat()))
    upsert_rows(conn, "tickers", COLUMNS, rows)
    return len(rows)
