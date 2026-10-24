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
            for k in [k for k in known if k != end and abs((k - end).days) <= SAME_QUARTER_DAYS]:
                del known[k]      # the same quarter with a slightly different end: newest key wins
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
    if len(q):
        qq = pd.merge_asof(base, q, on="filed")
    else:
        qq = base.assign(**{c: _empty([c])[c].reindex(base.index)
                            for c in QUARTER_COLUMNS[1:]}, q_filed=pd.NaT).astype(
            {"period_end": _DT, "q_filed": _DT, "fallback": "boolean"})
    if len(a):
        aa = pd.merge_asof(base, a, on="filed")
    else:
        aa = base.assign(a_end=pd.NaT, a_ttm=np.nan, a_fallback=False, a_filed=pd.NaT).astype(
            {"a_end": _DT, "a_filed": _DT, "a_fallback": "boolean"})
    has_annual = aa["a_ttm"].notna()
    margin = pd.Timedelta(days=SAME_QUARTER_DAYS)
    newer_annual = aa["a_end"].notna() & (aa["a_end"] > pd.to_datetime(qq["period_end"]) + margin)
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


def instant_steps(facts: pd.DataFrame, keep_updates: bool = False) -> pd.DataFrame:
    """Balance-sheet values per filing. By default a date keeps its first report (later
    restatements are ignored). `keep_updates=True` lets every row update its date in filing
    order; `total_debt` output needs it, as its rows are first filings of its components."""
    if keep_updates:
        d = facts[facts["period_start"].isna()].sort_values(
            ["filed", "accession"] if "accession" in facts else ["filed"])
    else:
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
    Used for weighted-average diluted shares. For one period end the shortest duration wins
    (a quarter over its year-to-date or annual figure); ties go to the earlier filing."""
    if facts.empty:
        return _empty(LATEST_COLUMNS)
    d = facts.sort_values(["filed", "accession"] if "accession" in facts else ["filed"])
    d = d.drop_duplicates(["period_start", "period_end"], keep="first")
    start = d["period_start"].fillna(d["period_end"])
    d = d.assign(_dur=(d["period_end"] - start).dt.days)
    known: dict[pd.Timestamp, tuple[int, float]] = {}
    rows = []
    for filed, batch in d.groupby("filed", sort=True):
        for end, dur, value in zip(batch["period_end"], batch["_dur"], batch["value"], strict=True):
            if end not in known or dur < known[end][0]:
                known[end] = (int(dur), float(value))
        last = max(known)
        rows.append((filed, last, known[last][1]))
    return pd.DataFrame(rows, columns=LATEST_COLUMNS)


def first_versions(facts: pd.DataFrame) -> pd.DataFrame:
    """The earliest filed version of each (period_start, period_end), ties by accession. Feed
    this to `discrete_quarters` and `annual_steps` so a value enters on its first report and a
    later re-report (a 10-K repeating a quarter) cannot change when it became known."""
    if facts.empty:
        return facts
    sort = ["filed", "accession"] if "accession" in facts else ["filed"]
    return (facts.sort_values(sort, kind="stable")
            .drop_duplicates(["period_start", "period_end"], keep="first"))


def total_debt(long_term: pd.DataFrame, short_term: pd.DataFrame) -> pd.DataFrame:
    """Debt per balance-sheet date, as facts-shaped rows. A row is emitted at each component's
    first filing: the line filed first alone, then (when the other line arrives later) the sum.
    `LongTermDebt` already includes the current portion, so a short-term line is added to it
    only when it is borrowings or commercial paper; otherwise the short line is ignored and does
    not delay `filed`. A date can therefore have two rows: pass the result to
    `instant_steps(..., keep_updates=True)` so the later, complete value replaces the first."""
    cols = ["period_end", "value", "filed", "tag"]
    lt = _first_reports(long_term)[cols] if len(long_term) else pd.DataFrame(columns=cols)
    st = _first_reports(short_term)[cols] if len(short_term) else pd.DataFrame(columns=cols)
    rows = []
    m = lt.merge(st, on="period_end", how="outer", suffixes=("_l", "_s"))
    for r in m.itertuples():
        has_l, has_s = pd.notna(r.value_l), pd.notna(r.value_s)
        if has_l and has_s and r.tag_l == TOTAL_DEBT_TAG and r.tag_s not in SEPARATE_SHORT_TAGS:
            rows.append((r.period_end, float(r.value_l), r.filed_l))
        elif has_l and has_s:
            first, second = sorted([(r.filed_l, float(r.value_l)), (r.filed_s, float(r.value_s))])
            total = float(r.value_l) + float(r.value_s)
            if first[0] < second[0]:
                rows.append((r.period_end, first[1], first[0]))
            rows.append((r.period_end, total, second[0]))
        elif has_l:
            rows.append((r.period_end, float(r.value_l), r.filed_l))
        else:
            rows.append((r.period_end, float(r.value_s), r.filed_s))
    out = pd.DataFrame(rows, columns=["period_end", "value", "filed"])
    out.insert(0, "period_start", pd.Series(pd.NaT, index=out.index, dtype=_DT))
    out["period_end"] = pd.to_datetime(out["period_end"]).astype(_DT)
    out["filed"] = pd.to_datetime(out["filed"]).astype(_DT)
    out["value"] = out["value"].astype("float64")
    return out.sort_values(["filed", "period_end"], kind="stable").reset_index(drop=True)


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
    """`primary` plus the rows of `extra` that were known before `primary` knew the period: a row
    is kept when no primary row for that quarter (ends within SAME_QUARTER_DAYS) was filed at or
    before it. The primary row later replaces the value at its own filing (`quarter_steps`
    treats ends within SAME_QUARTER_DAYS as one quarter). Ends are not snapped here: that would
    depend on primary rows filed after the extra row."""
    if extra.empty:
        return primary
    ends = primary["period_end"].to_numpy()
    pfiled = primary["filed"].to_numpy()
    keep = []
    for e, f in zip(extra["period_end"].to_numpy(), extra["filed"].to_numpy(), strict=True):
        near = np.abs((ends - e) / np.timedelta64(1, "D")) <= SAME_QUARTER_DAYS
        keep.append(not near.any() or bool(f < pfiled[near].min()))
    return pd.concat([primary, extra[keep][primary.columns]], ignore_index=True)


def fallback_ends(facts: pd.DataFrame, fallback_tags: Iterable[str]) -> frozenset:
    """Period ends whose first-filed version (per period, ties by accession) is a summed or
    fallback row (companyfacts._fallback_rows). Later versions do not change the answer."""
    if facts.empty:
        return frozenset()
    tags = set(fallback_tags)
    first = first_versions(facts)
    mask = first["tag"].str.contains("+", regex=False) | first["tag"].isin(tags)
    return frozenset(first.loc[mask, "period_end"])
