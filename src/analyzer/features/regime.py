"""Liquidity regime (spec 5.6): context for research breakdowns only, never a gate or a score.

FRED dates a value by its observation period, not by when it was published. Each series is
shifted by a conservative publication lag so a value is never used before it existed (first
release values from ALFRED are deferred; addendum 7.3)."""

from __future__ import annotations

import pandas as pd

from analyzer.config import RegimeRules

# Facts about FRED publication schedules, not tunable thresholds, so they stay in code.
PUBLICATION_LAG_DAYS = {
    "WALCL": 7,          # weekly, Wednesday level, published Thursday
    "RRPONTSYD": 1,      # daily
    "M2SL": 58,          # monthly, dated the 1st, published about four weeks after month end
    "NFCI": 7,           # weekly, dated Friday, published the next Wednesday
    "BAMLH0A0HYM2": 1,   # daily
    "DFII10": 1,         # daily
}


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


# Each helper returns (indicator, known): indicator is True when the series is "loose",
# known is True when the indicator can be computed on that date.

def _below_threshold(x: pd.Series, value: float) -> tuple[pd.Series, pd.Series]:
    return x < value, x.notna()


def _below_rolling_median(x: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    median = x.rolling(window, min_periods=window).median()
    return x < median, median.notna() & x.notna()


def _moved_over(x: pd.Series, window: int, direction: int) -> tuple[pd.Series, pd.Series]:
    """Loose when the level moved up (direction +1) or down (-1) over `window` days.
    For a positive series a relative change and the plain difference have the same sign."""
    change = x - x.shift(window)
    return direction * change > 0, change.notna()


def regime(macro: pd.DataFrame, rules: RegimeRules) -> pd.Series:
    parts = {
        "nfci": _below_threshold(macro["NFCI"], 0.0),
        "hy": _below_rolling_median(macro["BAMLH0A0HYM2"], rules.median_window),
        "fed": _moved_over(macro["WALCL"], rules.change_window, +1),
        "m2": _moved_over(macro["M2SL"], rules.yoy_window, +1),
        "rrp": _moved_over(macro["RRPONTSYD"], rules.change_window, -1),
        "real": _below_rolling_median(macro["DFII10"], rules.median_window),
    }
    indicators = pd.DataFrame({k: v[0] for k, v in parts.items()}, index=macro.index)
    known = pd.DataFrame({k: v[1] for k, v in parts.items()}, index=macro.index)
    loose = indicators.sum(axis=1)
    out = pd.Series("neutral", index=macro.index)
    out[loose >= rules.loose_min] = "loose"
    out[loose <= rules.tight_max] = "tight"
    out[~known.all(axis=1)] = "unknown"
    return out
