"""Point-in-time reads of stored facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from analyzer.data.edgar.tags import CONCEPT_BY_NAME

_OUT = ["cik", "tag", "period_start", "period_end", "value", "filed", "accession"]


def load_concept(
    conn,
    cik: int,
    concept: str,
    as_of: date | None = None,
    merges: Mapping[int, Sequence[int]] | None = None,
) -> pd.DataFrame:
    """One value per period, as it was known on `as_of` (default: everything filed)."""
    ciks = [cik, *(merges or {}).get(cik, [])]
    sql = (f"SELECT {', '.join(_OUT)} FROM facts WHERE concept = ? "
           f"AND cik IN ({', '.join('?' * len(ciks))})")
    params: list = [concept, *ciks]
    if as_of is not None:
        sql += " AND filed <= ?"
        params.append(as_of.isoformat())
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=_OUT)
    priority = {t: i for i, t in enumerate(CONCEPT_BY_NAME[concept].tags)}
    df["prio"] = df["tag"].map(priority).fillna(len(priority))
    df["foreign"] = (df["cik"] != cik).astype(int)
    # Latest filing wins; accession breaks same-day ties (a later accession is a later filing).
    df = df.sort_values(["period_start", "period_end", "foreign", "prio", "filed", "accession"],
                        ascending=[True, True, True, True, False, False])
    df = df.drop_duplicates(["period_start", "period_end"], keep="first")
    df["period_start"] = pd.to_datetime(df["period_start"].mask(df["period_start"] == ""))
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["filed"] = pd.to_datetime(df["filed"])
    return df.sort_values(["period_end", "period_start"]).reset_index(drop=True)[_OUT]


def latest_shares(conn, cik: int, as_of: date,
                  merges: Mapping[int, Sequence[int]] | None = None) -> float | None:
    """Shares outstanding from the latest filing known on `as_of`."""
    for concept in ("shares_outstanding", "diluted_shares"):
        df = load_concept(conn, cik, concept, as_of=as_of, merges=merges)
        if not df.empty:
            return float(df.sort_values(["period_end", "filed"]).iloc[-1]["value"])
    return None
