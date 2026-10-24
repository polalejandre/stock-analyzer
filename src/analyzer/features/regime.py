"""Liquidity regime (spec 5.6): context for research breakdowns only, never a gate or a score.

FRED dates a value by its observation period, not by when it was published. Each series is
shifted by a conservative publication lag so a value is never used before it existed (first
release values from ALFRED are deferred; addendum 7.3)."""

from __future__ import annotations

import pandas as pd

PUBLICATION_LAG_DAYS = {
    "WALCL": 7,          # weekly, Wednesday level, published Thursday
    "RRPONTSYD": 1,      # daily
    "M2SL": 58,          # monthly, dated the 1st, published about four weeks after month end
    "NFCI": 7,           # weekly, dated Friday, published the next Wednesday
    "BAMLH0A0HYM2": 1,   # daily
    "DFII10": 1,         # daily
}
YEARS3 = 756   # trading days
WEEKS13 = 65


def load_macro(conn, dates: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT series_id, date, value FROM macro", conn)
    out = pd.DataFrame(index=dates)
    for series, lag in PUBLICATION_LAG_DAYS.items():
        s = df[df["series_id"] == series]
        if s.empty:
            out[series] = float("nan")
            continue
        known = pd.Series(s["value"].to_numpy(),
                          index=pd.to_datetime(s["date"]) + pd.Timedelta(days=lag)).sort_index()
        known = known[~known.index.duplicated(keep="last")]
        out[series] = known.reindex(known.index.union(dates)).ffill().reindex(dates)
    return out


def regime(macro: pd.DataFrame) -> pd.Series:
    hy, real = macro["BAMLH0A0HYM2"], macro["DFII10"]
    indicators = pd.DataFrame({
        "nfci": macro["NFCI"] < 0,
        "hy": hy < hy.rolling(YEARS3, min_periods=YEARS3).median(),
        "fed": macro["WALCL"] - macro["WALCL"].shift(WEEKS13) > 0,
        "m2": macro["M2SL"] / macro["M2SL"].shift(252) - 1 > 0,
        "rrp": macro["RRPONTSYD"] < macro["RRPONTSYD"].shift(WEEKS13),
        "real": real < real.rolling(YEARS3, min_periods=YEARS3).median(),
    })
    known = pd.DataFrame({
        "nfci": macro["NFCI"].notna(),
        "hy": hy.rolling(YEARS3, min_periods=YEARS3).median().notna() & hy.notna(),
        "fed": macro["WALCL"].shift(WEEKS13).notna(),
        "m2": macro["M2SL"].shift(252).notna(),
        "rrp": macro["RRPONTSYD"].shift(WEEKS13).notna(),
        "real": real.rolling(YEARS3, min_periods=YEARS3).median().notna() & real.notna(),
    })
    loose = indicators.sum(axis=1)
    out = pd.Series("neutral", index=macro.index)
    out[loose >= 4] = "loose"
    out[loose <= 2] = "tight"
    out[~known.all(axis=1)] = "unknown"
    return out
