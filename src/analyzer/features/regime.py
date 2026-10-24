"""Liquidity regime (spec 5.6): context for research breakdowns only, never a gate or a score.

FRED dates a value by its observation period, not by when it was published. Each series is
shifted by a conservative publication lag so a value is never used before it existed (first
release values from ALFRED are deferred; addendum 7.3). A value goes stale when its series has not
updated for three times its cadence (MAX_AGE_DAYS); the indicator is then unknown that day. The
regime is classified from the indicators known that day when at least `min_known` are."""

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
# Calendar days since the last value became known after which it is stale: three times the
# series cadence (daily 5, weekly 21, monthly 93).
MAX_AGE_DAYS = {"WALCL": 21, "RRPONTSYD": 5, "M2SL": 93, "NFCI": 21, "BAMLH0A0HYM2": 5,
                "DFII10": 5}
AGE_SUFFIX = "_age"


def load_macro(conn, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Each series as known on each date (forward-filled), plus `<series>_age`: calendar days since
    that value became known (NaN before the first)."""
    df = pd.read_sql_query("SELECT series_id, date, value FROM macro", conn)
    out = pd.DataFrame(index=dates)
    for series, lag in PUBLICATION_LAG_DAYS.items():
        s = df[df["series_id"] == series]
        if s.empty:
            out[series] = float("nan")
            out[series + AGE_SUFFIX] = float("nan")
            continue
        known = pd.Series(s["value"].to_numpy(),
                          index=pd.to_datetime(s["date"]) + pd.Timedelta(days=lag)).sort_index()
        known = known[~known.index.duplicated(keep="last")]
        grid = known.index.union(dates)
        out[series] = known.reindex(grid).ffill().reindex(dates)
        since = pd.Series(known.index, index=known.index).reindex(grid).ffill().reindex(dates)
        out[series + AGE_SUFFIX] = (dates - pd.DatetimeIndex(since)).days.astype("float64")
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


def _fresh(macro: pd.DataFrame, series: str) -> pd.Series:
    """False where the series' latest value is stale (a frame without the age column: fresh)."""
    age = macro.get(series + AGE_SUFFIX)
    if age is None:
        return pd.Series(True, index=macro.index)
    return age <= MAX_AGE_DAYS[series]


def regime(macro: pd.DataFrame, rules: RegimeRules) -> pd.Series:
    source = {"nfci": "NFCI", "hy": "BAMLH0A0HYM2", "fed": "WALCL", "m2": "M2SL",
              "rrp": "RRPONTSYD", "real": "DFII10"}
    parts = {
        "nfci": _below_threshold(macro["NFCI"], 0.0),
        "hy": _below_rolling_median(macro["BAMLH0A0HYM2"], rules.median_window),
        "fed": _moved_over(macro["WALCL"], rules.change_window, +1),
        "m2": _moved_over(macro["M2SL"], rules.yoy_window, +1),
        "rrp": _moved_over(macro["RRPONTSYD"], rules.change_window, -1),
        "real": _below_rolling_median(macro["DFII10"], rules.median_window),
    }
    known = pd.DataFrame({k: v[1] & _fresh(macro, source[k]) for k, v in parts.items()},
                         index=macro.index)
    indicators = pd.DataFrame({k: v[0] for k, v in parts.items()}, index=macro.index) & known
    n, k, loose = len(parts), known.sum(axis=1), indicators.sum(axis=1)
    # the six-indicator thresholds as shares of the indicators known, in integers:
    # loose >= ceil(loose_min / n * k)  <=>  n * loose >= loose_min * k (likewise for tight)
    out = pd.Series("neutral", index=macro.index)
    out[n * loose >= rules.loose_min * k] = "loose"
    out[n * loose <= rules.tight_max * k] = "tight"
    out[k < rules.min_known] = "unknown"
    return out
