"""Wide price panels: one row per trading date, one column per symbol."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date

import pandas as pd

ANCHOR = "SPY"
_CHUNK = 900  # SQLite allows 999 host parameters by default
_DT = "datetime64[ns]"
FIELDS = ("open", "high", "low", "close", "adj_close", "volume")


@dataclass(frozen=True)
class PricePanels:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    adj_close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def adj_open(self) -> pd.DataFrame:
        """The open on the dividend-adjusted basis of `adj_close`."""
        return self.open * (self.adj_close / self.close)

    def upto(self, end) -> PricePanels:
        end = pd.Timestamp(end)
        return PricePanels(**{f.name: getattr(self, f.name).loc[:end] for f in fields(self)})

    def select(self, symbols: Sequence[str]) -> PricePanels:
        cols = list(symbols)
        return PricePanels(**{f.name: getattr(self, f.name).reindex(columns=cols)
                              for f in fields(self)})


def trading_dates(conn, start: date, end: date, anchor: str = ANCHOR) -> pd.DatetimeIndex:
    rows = conn.execute("SELECT date FROM prices WHERE symbol = ? AND date >= ? AND date <= ? "
                        "ORDER BY date", (anchor, start.isoformat(), end.isoformat())).fetchall()
    return pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]).astype(_DT), name="date")


def load_price_panels(conn, symbols: Sequence[str], dates: pd.DatetimeIndex) -> PricePanels:
    symbols = list(dict.fromkeys(symbols))
    first, last = dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")
    parts = []
    for i in range(0, len(symbols), _CHUNK):
        chunk = symbols[i:i + _CHUNK]
        parts.append(pd.read_sql_query(
            f"SELECT symbol, date, {', '.join(FIELDS)} FROM prices "
            f"WHERE symbol IN ({', '.join('?' * len(chunk))}) AND date >= ? AND date <= ?",
            conn, params=[*chunk, first, last]))
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["symbol", "date", *FIELDS])
    df["date"] = pd.to_datetime(df["date"]).astype(_DT)
    df["adj_close"] = df["adj_close"].fillna(df["close"])
    out = {}
    for f in FIELDS:
        wide = df.pivot(index="date", columns="symbol", values=f) if len(df) else pd.DataFrame()
        out[f] = wide.reindex(index=dates, columns=symbols).astype("float64")
    return PricePanels(**out)
