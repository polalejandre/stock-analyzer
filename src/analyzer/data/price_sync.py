"""Store daily prices and keep them current with the fewest downloads.

Storage rules that keep adjusted and unadjusted data from mixing:
- `close` is split-adjusted, `adj_close` is split- and dividend-adjusted; only the primary
  provider supplies the latter. Fallback rows get an `adj_close` derived from the latest
  stored adjustment ratio, and a fallback never rewrites history that is already stored.
- A split or dividend rewrites the whole adjusted history, so when overlapping days no longer
  match what is stored, the symbol's full history is re-downloaded and replaced.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.prices import PRICE_COLUMNS, PriceProvider, fetch_with_fallback
from analyzer.store.db import upsert_rows

HISTORY_START = date(2009, 1, 1)
OVERLAP_DAYS = 14
ADJ_TOLERANCE = 1e-4
STALE_AFTER_DAYS = 365

_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
_NUMERIC = ["open", "high", "low", "close", "adj_close", "volume"]


def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _clean(v):
    return None if v is None or pd.isna(v) else float(v)


def _usable(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows with a close price; `close` is NOT NULL in storage. Providers already drop the rest."""
    return frame.dropna(subset=["close"])


def _rows(symbol: str, frame: pd.DataFrame, source: str):
    for r in _usable(frame).itertuples(index=False):
        yield (symbol, _iso(r.date), _clean(r.open), _clean(r.high), _clean(r.low),
               float(r.close), _clean(r.adj_close), _clean(r.volume), source)


def read_prices(conn: sqlite3.Connection, symbol: str, start=None, end=None) -> pd.DataFrame:
    sql = ("SELECT date, open, high, low, close, adj_close, volume, source "
           "FROM prices WHERE symbol = ?")
    params: list = [symbol]
    if start is not None:
        sql += " AND date >= ?"
        params.append(_iso(start))
    if end is not None:
        sql += " AND date <= ?"
        params.append(_iso(end))
    df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
    # Same dtypes as provider frames whatever the result (empty, all-NULL column, pandas default unit).
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    for col in _NUMERIC:
        df[col] = df[col].astype("float64")
    return df


def write_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    upsert_rows(conn, "prices", _COLUMNS, _rows(symbol, frame, source))


def replace_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    rows = list(_rows(symbol, frame, source))  # build first: a bad frame must not touch the table
    with conn:
        conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))
        conn.executemany(
            f"INSERT OR REPLACE INTO prices ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            rows,
        )


def last_dates(conn, symbols: Sequence[str]) -> dict[str, date]:
    wanted = set(symbols)
    rows = conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol").fetchall()
    return {s: date.fromisoformat(d) for s, d in rows if s in wanted}


def latest_market_date(conn) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM prices WHERE symbol = 'SPY'").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _latest_ratio(conn, symbol: str) -> float:
    """adj_close / close of the latest stored bar, or 1.0 when nothing usable is stored."""
    row = conn.execute(
        "SELECT close, adj_close FROM prices WHERE symbol = ? AND adj_close IS NOT NULL "
        "AND close > 0 ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return row[1] / row[0] if row else 1.0


def _with_adj(frame: pd.DataFrame, ratio: float = 1.0) -> pd.DataFrame:
    """Fill missing adj_close as close x ratio (fallback providers have no dividend adjustment)."""
    if not frame["adj_close"].isna().any():
        return frame
    frame = frame.copy()
    frame["adj_close"] = frame["adj_close"].fillna(frame["close"] * ratio)
    return frame


def _drift(new: pd.Series, old: pd.Series) -> float:
    """Largest relative change between matching values; NaN and non-positive olds are ignored."""
    return ((new / old.where(old > 0)) - 1).abs().max()


def _adjustment_changed(stored: pd.DataFrame, fresh: pd.DataFrame, primary: str) -> bool:
    """True when overlapping days from the primary no longer match what is stored."""
    if stored.empty:
        return False
    # The most recent stored day, whatever its source, may be a partial intraday bar.
    stored = stored[stored["date"] < stored["date"].max()]
    # Only primary-sourced rows carry true adjusted values; fallback rows are approximations.
    stored = stored[stored["source"] == primary]
    merged = stored.merge(fresh[PRICE_COLUMNS], on="date", suffixes=("_old", "_new"))
    return any(
        _drift(merged[f"{col}_new"], merged[f"{col}_old"]) > ADJ_TOLERANCE
        for col in ("close", "adj_close")
    )


@dataclass
class PriceSyncReport:
    updated: list[str] = field(default_factory=list)
    refetched: list[str] = field(default_factory=list)
    via_fallback: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    historical_missing: list[str] = field(default_factory=list)


def sync_prices(
    conn,
    providers: Sequence[PriceProvider],
    active: Sequence[str],
    historical: Sequence[str],
    today: date,
    history_start: date = HISTORY_START,
) -> PriceSyncReport:
    primary = providers[0]
    report = PriceSyncReport()
    active = list(dict.fromkeys(active))
    active_set = set(active)
    everything = list(dict.fromkeys([*active, *historical]))
    known = last_dates(conn, everything)
    stale_cutoff = today - timedelta(days=STALE_AFTER_DAYS)

    full = [s for s in everything
            if s not in known or (s in active_set and known[s] < stale_cutoff)]
    incremental = [s for s in active if s in known and s not in full]

    if full:
        res = fetch_with_fallback(providers, full, history_start, today)
        missing = list(res.missing)
        for sym, frame in res.frames.items():
            if _usable(frame).empty:
                missing.append(sym)
                continue
            # Replaced rows are discarded, so no stored ratio applies: nothing stored -> adj = close.
            replace_prices(conn, sym, _with_adj(frame), res.sources[sym])
            report.updated.append(sym)
            if res.sources[sym] != primary.name:
                report.via_fallback[sym] = res.sources[sym]
        report.missing += [s for s in missing if s in active_set]
        report.historical_missing += [s for s in missing if s not in active_set]

    if incremental:
        start = min(known[s] for s in incremental) - timedelta(days=OVERLAP_DAYS)
        res = fetch_with_fallback(providers, incremental, start, today)
        report.missing += res.missing
        stale: list[str] = []
        for sym, frame in res.frames.items():
            frame = _usable(frame)
            source = res.sources[sym]
            if source == primary.name:
                if not frame.empty and _adjustment_changed(
                    read_prices(conn, sym, start=frame["date"].min()), frame, primary.name
                ):
                    stale.append(sym)
                    continue
            else:
                # A fallback only extends history. Bars before the last stored one keep their
                # primary values, source and adjustment; the last one is refreshed (it may be partial).
                frame = frame[frame["date"] >= pd.Timestamp(known[sym])]
            if frame.empty:
                report.missing.append(sym)
                continue
            write_prices(conn, sym, _with_adj(frame, _latest_ratio(conn, sym)), source)
            report.updated.append(sym)
            if source != primary.name:
                report.via_fallback[sym] = source
        if stale:
            refreshed = fetch_with_fallback([primary], stale, history_start, today)
            for sym in stale:
                frame = refreshed.frames.get(sym)
                if frame is None or _usable(frame).empty:
                    report.missing.append(sym)  # stored rows stay as they were
                    continue
                replace_prices(conn, sym, _with_adj(frame), primary.name)
                report.refetched.append(sym)
    return report
