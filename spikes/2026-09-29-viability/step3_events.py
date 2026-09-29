# THROWAWAY: 8-K earnings releases (Item 2.02) and Form 4 open-market buys (code P) from EDGAR.
import re, xml.etree.ElementTree as ET, pandas as pd
from common import sec_get, sec_get_text, cik_map, UNIVERSE

def filings(ticker):
    cik = cik_map()[ticker]
    d = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", f"sub_{ticker}.json")
    r = pd.DataFrame(d["filings"]["recent"])
    r["filingDate"] = pd.to_datetime(r["filingDate"])
    return cik, r

def form4_buys(ticker, since="2025-09-29", max_docs=60):
    cik, r = filings(ticker)
    f4 = r[(r["form"] == "4") & (r["filingDate"] >= since)].head(max_docs)
    buys, parsed, failed = [], 0, 0
    for row in f4.itertuples():
        acc = row.accessionNumber.replace("-", "")
        doc = re.sub(r"^xsl[^/]+/", "", row.primaryDocument)  # raw XML, not XSL-rendered view
        try:
            x = sec_get_text(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}", f"f4_{acc}.xml")
            root = ET.fromstring(x)
            parsed += 1
        except Exception:
            failed += 1; continue
        owner = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName")
        title = root.findtext(".//reportingOwnerRelationship/officerTitle") or ""
        for t in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
            if t.findtext(".//transactionCoding/transactionCode") == "P":
                sh = float(t.findtext(".//transactionShares/value") or 0)
                px = float(t.findtext(".//transactionPricePerShare/value") or 0)
                buys.append(dict(ticker=ticker, date=t.findtext(".//transactionDate/value"), owner=owner,
                                 title=title, usd=round(sh * px)))
    return len(f4), parsed, failed, buys

rows, allbuys = [], []
for t in UNIVERSE:
    cik, r = filings(t)
    yr = r[r["filingDate"] >= "2025-09-29"]
    k8 = yr[yr["form"] == "8-K"]
    earn = k8[k8["items"].fillna("").str.contains("2.02")]
    rows.append(dict(ticker=t, eightK=len(k8), earnings_202=len(earn),
                     last_earn=earn["filingDate"].max().date() if len(earn) else None,
                     form4=int((yr["form"] == "4").sum())))
ev = pd.DataFrame(rows)
print(ev.to_string(index=False))
print("tickers w/ >=3 Item 2.02 8-Ks last 12m:", (ev.earnings_202 >= 3).sum(), "/", len(ev))

# Form 4 parse on a subset (rate-limit friendly)
for t in ["NFLX", "PYPL", "NKE", "LULU", "UNH", "INTC", "PFE", "EL", "DECK", "UPS"]:
    n, ok, bad, b = form4_buys(t)
    allbuys += b
    print(f"{t}: form4 last12m={n} parsed={ok} failed={bad} open-market buys={len(b)}")
print(pd.DataFrame(allbuys).to_string(index=False) if allbuys else "no P buys")
