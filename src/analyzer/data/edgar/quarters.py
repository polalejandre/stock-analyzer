"""Discrete fiscal quarters from direct quarterly facts and year-to-date differences."""

from __future__ import annotations

from typing import NamedTuple

import pandas as pd

QUARTER_DAYS = (77, 120)  # inclusive day count, 11–17 weeks: 12/13/14/16/17-week quarters
START_DRIFT_DAYS = 3  # YTD facts whose start dates differ by at most this share one chain
SAME_QUARTER_DAYS = 7  # quarter ends this close together are one quarter, not two
_DTYPES = {
    "period_start": "datetime64[ns]",
    "period_end": "datetime64[ns]",
    "value": "float64",
    "filed": "datetime64[ns]",
    "days": "int64",
    "derived": "bool",
}
_OUT = list(_DTYPES)


class _Quarter(NamedTuple):
    start: pd.Timestamp
    end: pd.Timestamp
    value: float
    filed: pd.Timestamp
    derived: bool


def _is_quarter(days: int) -> bool:
    return QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def _chain_keys(starts: pd.Series) -> pd.Series:
    """One key per run of start dates that are at most START_DRIFT_DAYS apart."""
    uniq = starts.drop_duplicates().sort_values()
    gap = uniq.diff().dt.days
    ids = (gap.isna() | (gap > START_DRIFT_DAYS)).cumsum()
    return starts.map(dict(zip(uniq, ids, strict=True)))


def _prefer(a: _Quarter, b: _Quarter) -> bool:
    """True if `a` should replace `b`: direct beats derived, then the later filing wins."""
    return (not a.derived, a.filed) > (not b.derived, b.filed)


def _one_per_quarter(candidates: list[_Quarter]) -> list[_Quarter]:
    kept: list[_Quarter] = []
    for c in sorted(candidates, key=lambda q: q.end):
        if kept and (c.end - kept[-1].end).days <= SAME_QUARTER_DAYS:
            if _prefer(c, kept[-1]):
                kept[-1] = c
        else:
            kept.append(c)
    return kept


def _frame(quarters: list[_Quarter]) -> pd.DataFrame:
    rows = [(q.start, q.end, q.value, q.filed, (q.end - q.start).days + 1, q.derived)
            for q in quarters]
    out = pd.DataFrame(rows, columns=_OUT).astype(_DTYPES)
    return out.sort_values("period_end").reset_index(drop=True)


def discrete_quarters(df: pd.DataFrame, additive: bool = True) -> pd.DataFrame:
    if df.empty:
        return _frame([])
    d = df.dropna(subset=["period_start"]).copy()
    d["days"] = (d["period_end"] - d["period_start"]).dt.days + 1
    candidates: list[_Quarter] = []
    direct_ends = set()
    for r in d[d["days"].map(_is_quarter)].itertuples():
        candidates.append(_Quarter(r.period_start, r.period_end, r.value, r.filed, False))
        direct_ends.add(r.period_end)
    ytd = d[d["days"] >= QUARTER_DAYS[0]].copy()
    if additive and not ytd.empty:
        ytd["chain"] = _chain_keys(ytd["period_start"])
        for _key, chain in ytd.groupby("chain"):
            links = chain.sort_values(["period_end", "filed"]).drop_duplicates(
                "period_end", keep="last")
            prev = None
            for r in links.itertuples():
                if (prev is not None and r.period_end not in direct_ends
                        and _is_quarter((r.period_end - prev.period_end).days)):
                    candidates.append(_Quarter(
                        prev.period_end + pd.Timedelta(days=1),
                        r.period_end,
                        r.value - prev.value,
                        max(r.filed, prev.filed),
                        True,
                    ))
                prev = r
    return _frame(_one_per_quarter(candidates))
