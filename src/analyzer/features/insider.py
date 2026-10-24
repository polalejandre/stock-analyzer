"""Insider cluster buying during the drawdown window (spec 5.2). A purchase is known from its
Form 4 filing date, never its transaction date."""

from __future__ import annotations

import re

import pandas as pd

from analyzer.config import FeatureRules

EXECUTIVE = re.compile(
    r"\b(?:CEO|CFO|chief executive|chief financial|principal executive|principal financial)\b", re.I)


def load_purchases(conn) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT issuer_cik AS cik, filing_date, owner_names AS owner, officer_title, shares, price "
        "FROM insider_transactions WHERE trans_code = 'P' "
        "AND COALESCE(acquired_disposed, 'A') = 'A'", conn)
    df["filing_date"] = pd.to_datetime(df["filing_date"]).astype("datetime64[ns]")
    df["executive"] = df["officer_title"].fillna("").str.contains(EXECUTIVE)
    df["usd"] = df["shares"].fillna(0) * df["price"].fillna(0)
    return df[["cik", "filing_date", "owner", "executive", "usd"]]


def cluster_buying(rows: pd.DataFrame, purchases: pd.DataFrame, rules: FeatureRules) -> pd.DataFrame:
    out = pd.DataFrame({"buyers": 0, "executive_buy": False, "cluster": False}, index=rows.index)
    if rows.empty or purchases.empty:
        return out
    r = rows[["cik", "date", "window_start"]].dropna(subset=["cik"]).copy()
    r["cik"] = r["cik"].astype("int64")
    r["row"] = r.index
    p = purchases.assign(cik=purchases["cik"].astype("int64"))
    m = r.merge(p, on="cik")
    m = m[(m["filing_date"] >= m["window_start"]) & (m["filing_date"] <= m["date"])]
    if m.empty:
        return out
    buyers = m.groupby("row")["owner"].nunique()
    big_exec = (m["executive"] & (m["usd"] >= rules.insider_exec_min_usd)).groupby(m["row"]).any()
    out.loc[buyers.index, "buyers"] = buyers.astype(int)
    out.loc[big_exec.index, "executive_buy"] = big_exec.astype(bool)
    out["cluster"] = (out["buyers"] >= rules.insider_min_buyers) | out["executive_buy"]
    return out
