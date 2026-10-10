"""SEC ticker map and per-company submissions (name, SIC, filing list)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from analyzer.data.http import HttpClient
from analyzer.store.db import upsert_rows

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
FILING_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT", "4", "4/A"}


def load_ticker_map(sec: HttpClient) -> pd.DataFrame:
    d = sec.get_json(TICKERS_URL, cache_key="sec/company_tickers_exchange.json",
                     max_age=timedelta(days=1))
    df = pd.DataFrame(d["data"], columns=d["fields"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
    df["exchange"] = df["exchange"].fillna("")
    df["cik"] = df["cik"].astype(int)
    return df.drop_duplicates("ticker").reset_index(drop=True)[["cik", "name", "ticker", "exchange"]]


@dataclass
class Submissions:
    cik: int
    name: str | None
    sic: str | None
    sic_description: str | None
    filings: pd.DataFrame


def _filings_frame(block: dict) -> pd.DataFrame:
    n = len(block.get("accessionNumber", []))
    return pd.DataFrame(
        {
            "accession": block.get("accessionNumber", []),
            "form": block.get("form", []),
            "filing_date": block.get("filingDate", []),
            "report_date": block.get("reportDate", [None] * n),
            "items": block.get("items", [""] * n),
            "primary_document": block.get("primaryDocument", [None] * n),
        }
    )


def fetch_submissions(
    sec: HttpClient, cik: int, *, since: date = date(2013, 1, 1),
    max_age: timedelta | None = timedelta(hours=20),
) -> Submissions:
    d = sec.get_json(SUBMISSIONS_URL.format(cik=cik), cache_key=f"sec/submissions/{cik}.json",
                     max_age=max_age)
    frames = [_filings_frame(d["filings"]["recent"])]
    for page in d["filings"].get("files", []):
        if page.get("filingTo", "9999-12-31") < since.isoformat():
            continue
        older = sec.get_json(SUBMISSIONS_PAGE_URL.format(name=page["name"]),
                             cache_key=(f"sec/submissions/pages/{page['name']}"
                                      f"-{page.get('filingTo')}-{page.get('filingCount')}"))
        frames.append(_filings_frame(older))
    f = pd.concat(frames, ignore_index=True)
    f = f[f["form"].isin(FILING_FORMS) & (f["filing_date"] >= since.isoformat())]
    f = f.drop_duplicates("accession").reset_index(drop=True)
    return Submissions(cik, d.get("name"), d.get("sic") or None, d.get("sicDescription") or None, f)


def store_submissions(conn, subs: Submissions, now: datetime) -> None:
    upsert_rows(conn, "companies", ["cik", "name", "sic", "sic_description", "updated_at"],
                [(subs.cik, subs.name, subs.sic, subs.sic_description, now.isoformat())])
    upsert_rows(
        conn, "filings",
        ["accession", "cik", "form", "filing_date", "report_date", "items", "primary_document"],
        [(r.accession, subs.cik, r.form, r.filing_date, r.report_date or None, r.items or None,
          r.primary_document) for r in subs.filings.itertuples()],
    )
