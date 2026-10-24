"""Who is in the universe on each date, and static facts about each price symbol."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from analyzer.config import UniverseRules
from analyzer.universe.industry import sic_sector


def symbol_map(conn) -> dict[str, str]:
    """Every ticker ever in an index -> the symbol its prices are stored under. Tickers with a
    fate in member_fates (no prices, or the ticker now belongs to another company) are left out."""
    aliases = dict(conn.execute("SELECT ticker, price_symbol FROM ticker_aliases").fetchall())
    fated = {r[0] for r in conn.execute("SELECT ticker FROM member_fates")}
    priced = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices")}
    out: dict[str, str] = {}
    for (ticker,) in conn.execute("SELECT DISTINCT ticker FROM index_membership"):
        if ticker in fated:
            continue
        symbol = aliases.get(ticker, ticker)
        if symbol in priced:
            out[ticker] = symbol
    return out


def membership_panel(conn, dates: pd.DatetimeIndex, symbols_of: Mapping[str, str]) -> pd.DataFrame:
    cols = sorted(set(symbols_of.values()))
    pos = {s: i for i, s in enumerate(cols)}
    arr = np.zeros((len(dates), len(cols)), dtype=bool)
    for ticker, start, end in conn.execute(
            "SELECT ticker, start_date, end_date FROM index_membership"):
        symbol = symbols_of.get(ticker)
        if symbol is None:
            continue
        i0 = dates.searchsorted(pd.Timestamp(start))
        i1 = len(dates) if end is None else dates.searchsorted(pd.Timestamp(end))
        arr[i0:i1, pos[symbol]] = True
    return pd.DataFrame(arr, index=dates, columns=cols)


def liquidity_panel(close: pd.DataFrame, volume: pd.DataFrame, rules: UniverseRules) -> pd.DataFrame:
    """Average dollar volume over a full window of bars at least the minimum. A missing bar
    inside the window (delisted, data gap) makes the window incomplete, so the day fails."""
    n = rules.dollar_volume_days
    avg = (close * volume).rolling(n, min_periods=n).mean()
    return avg >= rules.min_dollar_volume


def load_entities(conn, symbols: Sequence[str]) -> pd.DataFrame:
    tickers = pd.read_sql_query(
        "SELECT t.ticker AS symbol, t.cik, t.gics_sector, t.sector_etf, t.industry_etf, c.sic "
        "FROM tickers t LEFT JOIN companies c ON c.cik = t.cik", conn).set_index("symbol")
    alias_cik = dict(conn.execute(
        "SELECT price_symbol, cik FROM ticker_aliases WHERE cik IS NOT NULL").fetchall())
    verified = {r[0] for r in conn.execute(
        "SELECT DISTINCT t.ticker FROM tickers t JOIN index_snapshots s "
        "ON s.ticker = t.ticker AND s.cik = t.cik")}
    verified |= {r[0] for r in conn.execute(
        "SELECT DISTINCT a.price_symbol FROM ticker_aliases a JOIN index_snapshots s "
        "ON s.ticker = a.ticker AND s.cik = a.cik")}
    rows = []
    for s in symbols:
        t = tickers.loc[s] if s in tickers.index else None
        cik = t["cik"] if t is not None and pd.notna(t["cik"]) else alias_cik.get(s)
        sic = t["sic"] if t is not None and isinstance(t["sic"], str) and t["sic"] else None
        sector = (t["gics_sector"] if t is not None and isinstance(t["gics_sector"], str)
                  else sic_sector(sic))
        sector_etf = t["sector_etf"] if t is not None and isinstance(t["sector_etf"], str) else None
        industry_etf = (t["industry_etf"] if t is not None and isinstance(t["industry_etf"], str)
                        else None)
        rows.append({
            "symbol": s,
            "cik": float(cik) if cik is not None else np.nan,
            "sic": sic,
            "sector": sector,
            "industry": f"sic3:{sic[:3]}" if sic and sic.isdigit() else None,
            "sector_etf": sector_etf,
            "industry_etf": industry_etf,
            "ref_etf": industry_etf or sector_etf or "SPY",
            "identity_verified": s in verified,
        })
    return pd.DataFrame(rows).set_index("symbol")
