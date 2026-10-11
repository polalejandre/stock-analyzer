"""Point-in-time reads of stored facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from analyzer.data.edgar.quarters import discrete_quarters
from analyzer.data.edgar.tags import CONCEPT_BY_NAME

_OUT = ["cik", "tag", "period_start", "period_end", "value", "filed", "accession"]
# One datetime unit for every frame the data layer returns (prices and quarters use it too), so
# that merges such as pd.merge_asof(prices, facts) work. Every date column is cast to it, so an
# all-NaT period_start and an empty result match as well.
_DT = "datetime64[ns]"


def _empty() -> pd.DataFrame:
    dtypes = {"cik": "int64", "tag": "str", "period_start": _DT, "period_end": _DT,
              "value": "float64", "filed": _DT, "accession": "str"}
    return pd.DataFrame({c: pd.Series(dtype=dtypes[c]) for c in _OUT})


def load_concept(
    conn,
    cik: int,
    concept: str,
    as_of: date | None = None,
    merges: Mapping[int, Sequence[int]] | None = None,
    all_versions: bool = False,
) -> pd.DataFrame:
    """One value per period, as it was known on `as_of` (default: everything filed).

    With `all_versions`, every filing's value of a period is kept (one per accession, its
    highest-priority tag), for callers that choose between the first report and a restatement.
    Raises KeyError for a concept name that is not in the tag map.
    """
    if concept not in CONCEPT_BY_NAME:
        raise KeyError(f"unknown concept {concept!r}")
    ciks = [cik, *(merges or {}).get(cik, [])]
    sql = (f"SELECT {', '.join(_OUT)} FROM facts WHERE concept = ? "
           f"AND cik IN ({', '.join('?' * len(ciks))})")
    params: list = [concept, *ciks]
    if as_of is not None:
        sql += " AND filed <= ?"
        params.append(as_of.isoformat())
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return _empty()
    priority = {t: i for i, t in enumerate(CONCEPT_BY_NAME[concept].tags)}
    df["prio"] = df["tag"].map(priority).fillna(len(priority))
    df["foreign"] = (df["cik"] != cik).astype(int)
    # Latest filing wins; accession breaks same-day ties (a later accession is a later filing).
    df = df.sort_values(["period_start", "period_end", "foreign", "prio", "filed", "accession"],
                        ascending=[True, True, True, True, False, False])
    if all_versions:
        own = df.groupby(["period_start", "period_end"])["foreign"].transform("min")
        df = df[df["foreign"] == own].drop_duplicates(
            ["period_start", "period_end", "accession"], keep="first")
    else:
        df = df.drop_duplicates(["period_start", "period_end"], keep="first")
    df["period_start"] = pd.to_datetime(df["period_start"].mask(df["period_start"] == "")).astype(_DT)
    df["period_end"] = pd.to_datetime(df["period_end"]).astype(_DT)
    df["filed"] = pd.to_datetime(df["filed"]).astype(_DT)
    return df.sort_values(["period_end", "period_start", "filed"]).reset_index(drop=True)[_OUT]


def load_quarters(
    conn,
    cik: int,
    concept: str,
    as_of: date | None = None,
    merges: Mapping[int, Sequence[int]] | None = None,
    additive: bool = True,
) -> pd.DataFrame:
    """Discrete fiscal quarters of a duration concept, as known on `as_of`, built from every
    filed version so that a later restatement is never mixed with an original value."""
    return discrete_quarters(
        load_concept(conn, cik, concept, as_of=as_of, merges=merges, all_versions=True),
        additive=additive)


def latest_shares_dated(conn, cik: int, as_of: date,
                        merges: Mapping[int, Sequence[int]] | None = None
                        ) -> tuple[float, date] | None:
    """(shares outstanding, the date the count is as of) from the latest filing known on `as_of`."""
    for concept in ("shares_outstanding", "diluted_shares"):
        df = load_concept(conn, cik, concept, as_of=as_of, merges=merges)
        if not df.empty:
            row = df.sort_values(["period_end", "filed"]).iloc[-1]
            return float(row["value"]), row["period_end"].date()
    return None


def latest_shares(conn, cik: int, as_of: date,
                  merges: Mapping[int, Sequence[int]] | None = None) -> float | None:
    """Shares outstanding from the latest filing known on `as_of`."""
    known = latest_shares_dated(conn, cik, as_of, merges)
    return None if known is None else known[0]
