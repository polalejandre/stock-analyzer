"""Point-in-time step series: one row per filing date, holding what was known after it.

Every value enters on its `filed` date. A quarter's value is the version `discrete_quarters`
keeps (the first report, or an amendment within a year), so it enters no earlier than that
version's filing. Annual and balance-sheet values keep their first report; later restatements
are ignored, which is conservative: nothing is ever known before it was filed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

from analyzer.data.edgar.quarters import QUARTER_DAYS, SAME_QUARTER_DAYS

ANNUAL_DAYS = (350, 380)
YEAR_TOLERANCE_DAYS = 20      # "the same quarter a year earlier" ends within this of end - 365
AVG_WINDOW_DAYS = 400         # balance-sheet average: period ends at most this before the latest
TOTAL_DEBT_TAG = "us-gaap:LongTermDebt"   # includes the current portion
SEPARATE_SHORT_TAGS = {"us-gaap:ShortTermBorrowings", "us-gaap:CommercialPaper"}
_DT = "datetime64[ns]"

QUARTER_COLUMNS = ["filed", "period_end", "ttm", "q0", "q0_ly", "q1", "q1_ly", "fallback"]
ANNUAL_COLUMNS = ["filed", "period_end", "ttm", "fallback"]
TTM_COLUMNS = ["filed", "value_filed", "period_end", "ttm", "q0", "q0_ly", "q1", "q1_ly",
               "fallback", "annual"]
INSTANT_COLUMNS = ["filed", "period_end", "value", "avg5"]
LATEST_COLUMNS = ["filed", "period_end", "value"]
PERIOD_COLUMNS = ["period_start", "period_end", "value", "filed", "days", "derived"]


def _empty(columns: list[str]) -> pd.DataFrame:
    dates = {"filed", "value_filed", "period_end", "period_start"}
    bools = {"fallback", "annual", "derived"}
    return pd.DataFrame({c: pd.Series(dtype=_DT if c in dates else bool if c in bools
                                      else "int64" if c == "days" else "float64")
                         for c in columns})


def _consecutive(ends: list[pd.Timestamp]) -> bool:
    return all(QUARTER_DAYS[0] <= (b - a).days <= QUARTER_DAYS[1]
               for a, b in zip(ends, ends[1:], strict=False))


def _year_ago(known: dict[pd.Timestamp, float], end: pd.Timestamp | None) -> float:
    if end is None:
        return math.nan
    target = end - pd.Timedelta(days=365)
    best = min(known, key=lambda e: abs((e - target).days))
    return known[best] if abs((best - target).days) <= YEAR_TOLERANCE_DAYS else math.nan


def quarter_steps(quarters: pd.DataFrame,
                  fallback_ends: frozenset = frozenset()) -> pd.DataFrame:
    if quarters.empty:
        return _empty(QUARTER_COLUMNS)
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in quarters.sort_values(["filed", "period_end"]).groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        ends = sorted(known)
        last4 = ends[-4:]
        complete = len(last4) == 4 and _consecutive(last4)
        q0 = ends[-1]
        q1 = ends[-2] if len(ends) >= 2 and _consecutive(ends[-2:]) else None
        rows.append((filed, q0, sum(known[e] for e in last4) if complete else math.nan,
                     known[q0], _year_ago(known, q0),
                     known[q1] if q1 is not None else math.nan, _year_ago(known, q1),
                     any(e in fallback_ends for e in last4)))
    return pd.DataFrame(rows, columns=QUARTER_COLUMNS)


def annual_steps(facts: pd.DataFrame, fallback_ends: frozenset = frozenset()) -> pd.DataFrame:
    d = facts.dropna(subset=["period_start"])
    if d.empty:
        return _empty(ANNUAL_COLUMNS)
    days = (d["period_end"] - d["period_start"]).dt.days + 1
    d = d[(days >= ANNUAL_DAYS[0]) & (days <= ANNUAL_DAYS[1])]
    d = d.sort_values(["filed", "accession"]).drop_duplicates("period_end", keep="first")
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        rows.append((filed, last, known[last], last in fallback_ends))
    return pd.DataFrame(rows, columns=ANNUAL_COLUMNS) if rows else _empty(ANNUAL_COLUMNS)


def combine_ttm(qsteps: pd.DataFrame, asteps: pd.DataFrame) -> pd.DataFrame:
    """At every filing date of either series: the quarterly TTM when its latest quarter ends no
    earlier than the latest fiscal year known, otherwise the annual value (`annual` = True).
    `value_filed` is when the value used was filed, for the age limit."""
    filed = sorted(set(qsteps["filed"]) | set(asteps["filed"]))
    if not filed:
        return _empty(TTM_COLUMNS)
    base = pd.DataFrame({"filed": pd.to_datetime(filed).astype(_DT)})
    q = qsteps.assign(q_filed=qsteps["filed"]).sort_values("filed")
    a = (asteps.rename(columns={"period_end": "a_end", "ttm": "a_ttm", "fallback": "a_fallback"})
         .assign(a_filed=asteps["filed"]).sort_values("filed"))
    qq = pd.merge_asof(base, q, on="filed") if len(q) else base.assign(
        **{c: np.nan for c in QUARTER_COLUMNS[1:]}, q_filed=pd.NaT)
    aa = pd.merge_asof(base, a, on="filed") if len(a) else base.assign(
        a_end=pd.NaT, a_ttm=np.nan, a_fallback=False, a_filed=pd.NaT)
    has_annual = aa["a_ttm"].notna()
    newer_annual = aa["a_end"].notna() & (aa["a_end"] > pd.to_datetime(qq["period_end"]))
    use_annual = has_annual & (qq["ttm"].isna() | newer_annual)
    out = pd.DataFrame({
        "filed": base["filed"],
        "value_filed": pd.to_datetime(np.where(use_annual, aa["a_filed"], qq["q_filed"])).astype(_DT),
        "period_end": pd.to_datetime(np.where(use_annual, aa["a_end"], qq["period_end"])).astype(_DT),
        "ttm": np.where(use_annual, aa["a_ttm"], qq["ttm"]).astype("float64"),
        "q0": qq["q0"].astype("float64"), "q0_ly": qq["q0_ly"].astype("float64"),
        "q1": qq["q1"].astype("float64"), "q1_ly": qq["q1_ly"].astype("float64"),
        "fallback": np.where(use_annual, aa["a_fallback"].astype("boolean").fillna(False),
                             qq["fallback"].astype("boolean").fillna(False)).astype(bool),
        "annual": use_annual.to_numpy(dtype=bool),
    })
    return out[TTM_COLUMNS]


def _first_reports(facts: pd.DataFrame) -> pd.DataFrame:
    """Instant facts, one per balance-sheet date: the first filed version."""
    d = facts[facts["period_start"].isna()]
    sort = ["filed", "accession"] if "accession" in d else ["filed"]
    return d.sort_values(sort).drop_duplicates("period_end", keep="first")


def instant_steps(facts: pd.DataFrame) -> pd.DataFrame:
    d = _first_reports(facts)
    if d.empty:
        return _empty(INSTANT_COLUMNS)
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        recent = [e for e in sorted(known)[-5:] if (last - e).days <= AVG_WINDOW_DAYS]
        rows.append((filed, last, known[last], float(np.mean([known[e] for e in recent]))))
    return pd.DataFrame(rows, columns=INSTANT_COLUMNS)


def latest_value_steps(facts: pd.DataFrame) -> pd.DataFrame:
    """Duration facts of any length: the value with the latest period end known at each filing.
    Used for weighted-average diluted shares."""
    if facts.empty:
        return _empty(LATEST_COLUMNS)
    d = facts.sort_values("filed").drop_duplicates(["period_start", "period_end"], keep="first")
    known: dict[pd.Timestamp, float] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, value in zip(batch["period_end"], batch["value"], strict=True):
            known[end] = float(value)
        last = max(known)
        rows.append((filed, last, known[last]))
    return pd.DataFrame(rows, columns=LATEST_COLUMNS)


def total_debt(long_term: pd.DataFrame, short_term: pd.DataFrame) -> pd.DataFrame:
    """Debt per balance-sheet date. `LongTermDebt` already includes the current portion, so a
    short-term line is added to it only when it is borrowings or commercial paper."""
    cols = ["period_end", "value", "filed", "tag"]
    lt = _first_reports(long_term)[cols] if len(long_term) else pd.DataFrame(columns=cols)
    st = _first_reports(short_term)[cols] if len(short_term) else pd.DataFrame(columns=cols)
    if lt.empty and st.empty:
        return pd.DataFrame({"period_start": pd.Series(dtype=_DT), "period_end": pd.Series(dtype=_DT),
                             "value": pd.Series(dtype="float64"), "filed": pd.Series(dtype=_DT)})
    m = lt.merge(st, on="period_end", how="outer", suffixes=("_l", "_s"))
    overlap = (m["tag_l"] == TOTAL_DEBT_TAG) & ~m["tag_s"].isin(SEPARATE_SHORT_TAGS)
    short = m["value_s"].astype("float64").fillna(0).where(~overlap, 0.0)
    value = m["value_l"].astype("float64").fillna(0) + short
    filed = pd.concat([pd.to_datetime(m["filed_l"]), pd.to_datetime(m["filed_s"])], axis=1).max(axis=1)
    return pd.DataFrame({"period_start": pd.Series(pd.NaT, index=m.index, dtype=_DT),
                         "period_end": pd.to_datetime(m["period_end"]).astype(_DT),
                         "value": value, "filed": filed.astype(_DT)})


def sum_periods(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Period-by-period sum of two duration series (bank revenue = NII + noninterest income),
    known once both parts are filed."""
    if a.empty or b.empty:
        return _empty(PERIOD_COLUMNS)
    m = a.merge(b, on=["period_start", "period_end"], suffixes=("_a", "_b"))
    out = pd.DataFrame({
        "period_start": m["period_start"], "period_end": m["period_end"],
        "value": m["value_a"] + m["value_b"],
        "filed": m[["filed_a", "filed_b"]].max(axis=1).astype(_DT),
    })
    out["days"] = (out["period_end"] - out["period_start"]).dt.days + 1
    out["derived"] = (m["derived_a"] | m["derived_b"]) if "derived_a" in m else False
    return out[PERIOD_COLUMNS]


def add_missing_periods(primary: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """`primary` plus the rows of `extra` whose period is not already in it."""
    if extra.empty:
        return primary
    ends = primary["period_end"].to_numpy()
    keep = [not np.any(np.abs((ends - e) / np.timedelta64(1, "D")) <= SAME_QUARTER_DAYS)
            for e in extra["period_end"].to_numpy()]
    return pd.concat([primary, extra[keep][primary.columns]], ignore_index=True)


def fallback_ends(facts: pd.DataFrame, fallback_tags: Iterable[str]) -> frozenset:
    """Period ends whose value came from a summed fallback row (companyfacts._fallback_rows)."""
    tags = set(fallback_tags)
    mask = facts["tag"].str.contains("+", regex=False) | facts["tag"].isin(tags)
    return frozenset(facts.loc[mask, "period_end"])
