"""Discrete fiscal quarters from direct quarterly facts and year-to-date differences."""

from __future__ import annotations

import pandas as pd

QUARTER_DAYS = (77, 120)  # 11–17 weeks: 12/13/14/16/17-week quarters
_OUT = ["period_start", "period_end", "value", "filed", "days", "derived"]


def _is_quarter(days: int) -> bool:
    return QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def discrete_quarters(df: pd.DataFrame, additive: bool = True) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=_OUT)
    d = df.dropna(subset=["period_start"]).copy()
    d["days"] = (d["period_end"] - d["period_start"]).dt.days
    quarters: dict[pd.Timestamp, tuple] = {}
    for r in d[d["days"].map(_is_quarter)].itertuples():
        quarters[r.period_end] = (r.period_start, r.value, r.filed, False)
    if additive:
        for _start, chain in d[d["days"] >= QUARTER_DAYS[0]].groupby("period_start"):
            prev = None
            for r in chain.sort_values("period_end").itertuples():
                if prev is not None and r.period_end not in quarters:
                    if _is_quarter((r.period_end - prev.period_end).days):
                        quarters[r.period_end] = (
                            prev.period_end + pd.Timedelta(days=1),
                            r.value - prev.value,
                            max(r.filed, prev.filed),
                            True,
                        )
                prev = r
    out = pd.DataFrame(
        [(s, e, v, f, (e - s).days + 1, derived)  # inclusive day count, same for both kinds
         for e, (s, v, f, derived) in quarters.items()],
        columns=_OUT,
    )
    return out.sort_values("period_end").reset_index(drop=True)
