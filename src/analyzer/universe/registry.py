"""The `tickers` table: every stock the engine tracks, with CIK and reference ETFs."""

from __future__ import annotations

from datetime import date

import pandas as pd

from analyzer.store.db import upsert_rows
from analyzer.universe.industry import reference_etfs

COLUMNS = ["ticker", "cik", "name", "exchange", "gics_sector", "gics_sub_industry",
           "sector_etf", "industry_etf", "updated_at"]


def _clean(v):
    """NULL read through pandas arrives as NaN (truthy); normalize to None."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA or v == "":
        return None
    return v


def refresh_tickers(conn, ticker_map: pd.DataFrame, today: date) -> int:
    latest = pd.read_sql_query(
        "SELECT s.ticker, s.security, s.gics_sector, s.gics_sub_industry FROM index_snapshots s "
        "JOIN (SELECT ticker, MAX(snapshot_date) AS d FROM index_snapshots GROUP BY ticker) m "
        "ON m.ticker = s.ticker AND m.d = s.snapshot_date",
        conn,
    ).drop_duplicates("ticker").set_index("ticker")
    young_cik = dict(conn.execute("SELECT ticker, cik FROM young_listings").fetchall())
    young = set(young_cik)
    sec = ticker_map.set_index("ticker")
    sic = dict(conn.execute("SELECT cik, sic FROM companies").fetchall())

    rows = []
    for ticker in sorted(set(latest.index) | young):
        in_sec = ticker in sec.index
        in_latest = ticker in latest.index
        cik = _clean(sec.at[ticker, "cik"]) if in_sec else None
        if cik is None:
            cik = young_cik.get(ticker)
        cik = int(cik) if cik is not None else None
        name = _clean(sec.at[ticker, "name"]) if in_sec else None
        exchange = _clean(sec.at[ticker, "exchange"]) if in_sec else None
        sector = _clean(latest.at[ticker, "gics_sector"]) if in_latest else None
        sub = _clean(latest.at[ticker, "gics_sub_industry"]) if in_latest else None
        if name is None and in_latest:
            name = _clean(latest.at[ticker, "security"])
        ref = reference_etfs(sector, sub, sic.get(cik))
        rows.append((ticker, cik, name, exchange, sector, sub, ref.sector_etf, ref.industry_etf,
                     today.isoformat()))
    upsert_rows(conn, "tickers", COLUMNS, rows)
    return len(rows)
