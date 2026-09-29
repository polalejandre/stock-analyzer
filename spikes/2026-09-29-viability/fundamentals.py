# THROWAWAY SPIKE CODE — point-in-time quarterly fundamentals from SEC XBRL companyfacts.
from datetime import date
import pandas as pd
from common import sec_get, cik_map

CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense", "RegulatedAndUnregulatedOperatingRevenue",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "eps": ["EarningsPerShareDiluted"],
    "op_income": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}


def _facts(cf, names):
    """All USD (or USD/shares) duration facts for the first-listed concepts, merged."""
    rows = []
    for n in names:
        c = cf["facts"].get("us-gaap", {}).get(n)
        if not c:
            continue
        for unit, arr in c["units"].items():
            for f in arr:
                if "start" in f:
                    rows.append((f["start"], f["end"], f["val"], f["filed"], n))
    if not rows:
        return pd.DataFrame(columns=["start", "end", "val", "filed", "concept"])
    df = pd.DataFrame(rows, columns=["start", "end", "val", "filed", "concept"])
    for col in ("start", "end", "filed"):
        df[col] = pd.to_datetime(df[col])
    # point-in-time: keep FIRST reported value per period (ignore later restatements)
    df = df.sort_values("filed").drop_duplicates(["start", "end"], keep="first")
    df["days"] = (df["end"] - df["start"]).dt.days
    return df


def discrete_quarters(df):
    """Derive discrete quarters from direct 3M facts, or YTD differences (6M-3M, 9M-6M, 12M-9M)."""
    q = {}
    direct = df[df["days"].between(80, 100)]
    for r in direct.itertuples():
        q[r.end] = (r.val, r.filed)
    # YTD differencing: facts sharing a start date
    for start, g in df[df["days"] > 100].groupby("start"):
        g = g.sort_values("end")
        prev_end, prev_val, prev_filed = None, None, None
        base = df[(df["start"] == start) & df["days"].between(80, 100)]
        if len(base):
            b = base.iloc[0]
            prev_end, prev_val, prev_filed = b.end, b.val, b.filed
        for r in g.itertuples():
            if prev_end is not None and r.end not in q and 80 <= (r.end - prev_end).days <= 100:
                q[r.end] = (r.val - prev_val, max(r.filed, prev_filed))
            prev_end, prev_val, prev_filed = r.end, r.val, r.filed
    out = pd.DataFrame([(k, v[0], v[1]) for k, v in q.items()], columns=["end", "val", "filed"])
    return out.sort_values("end").reset_index(drop=True)


def quarterly_table(ticker):
    cik = cik_map()[ticker]
    cf = sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", f"cf_{ticker}.json")
    series = {}
    for key, names in CONCEPTS.items():
        # pick the concept with the most recent data (companies switch tags over time)
        best = None
        for n in names:
            d = _facts(cf, [n])
            if len(d) and (best is None or d["end"].max() > best["end"].max()):
                best = d
        if best is None:
            continue
        # also merge older data from other tags so history is long
        merged = _facts(cf, names)
        series[key] = discrete_quarters(merged if len(merged) else best)
    return series


def ttm_asof(series, key, asof):
    """TTM sum and YoY growth using only filings public on `asof`. Returns (ttm, ttm_yoy, last_q_end)."""
    s = series.get(key)
    if s is None or s.empty:
        return None, None, None
    s = s[s["filed"] <= asof].sort_values("end")
    if len(s) < 8:
        return None, None, None
    last4, prev4 = s.iloc[-4:], s.iloc[-8:-4]
    span = (last4["end"].iloc[-1] - last4["end"].iloc[0]).days
    if not 250 <= span <= 300:  # gaps in quarters → unreliable
        return None, None, None
    ttm, prev = last4["val"].sum(), prev4["val"].sum()
    yoy = (ttm - prev) / abs(prev) if prev else None
    return ttm, yoy, last4["end"].iloc[-1]


def snapshot(series, asof):
    asof = pd.Timestamp(asof)
    rev, rev_yoy, q_end = ttm_asof(series, "revenue", asof)
    ni, ni_yoy, _ = ttm_asof(series, "net_income", asof)
    oi, oi_yoy, _ = ttm_asof(series, "op_income", asof)
    ocf, _, _ = ttm_asof(series, "ocf", asof)
    capex, _, _ = ttm_asof(series, "capex", asof)
    fcf = ocf - capex if ocf is not None and capex is not None else None
    # latest single quarter revenue YoY (momentum of the latest report)
    q_yoy = None
    s = series.get("revenue")
    if s is not None:
        s = s[s["filed"] <= asof].sort_values("end")
        if len(s) >= 5 and 350 <= (s["end"].iloc[-1] - s["end"].iloc[-5]).days <= 380 and s["val"].iloc[-5]:
            q_yoy = s["val"].iloc[-1] / s["val"].iloc[-5] - 1
    return dict(q_end=q_end, rev_ttm=rev, rev_yoy=rev_yoy, rev_q_yoy=q_yoy, ni_ttm=ni, ni_yoy=ni_yoy,
                oi_ttm=oi, oi_yoy=oi_yoy, fcf_ttm=fcf,
                op_margin=(oi / rev) if oi is not None and rev else None)
