"""`analyzer audit`: per-ticker data coverage and the problems worth a look."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.edgar.facts_read import load_concept
from analyzer.data.edgar.quarters import discrete_quarters

STALE_PRICE_DAYS = 4
FCF_WINDOW_DAYS = 470  # four quarters plus filing lag


@dataclass
class TickerAudit:
    ticker: str
    cik: int | None
    price_first: date | None = None
    price_last: date | None = None
    revenue_quarters: int = 0
    first_revenue_quarter: date | None = None
    has_fcf: bool = False
    facts_behind_filing: bool = False
    sector_etf: str | None = None
    industry_etf: str | None = None
    flags: list[str] = field(default_factory=list)


def _quarters(conn, cik, concept, merges) -> pd.DataFrame:
    return discrete_quarters(load_concept(conn, cik, concept, merges=merges))


def _recent_count(q: pd.DataFrame, today: date) -> int:
    if q.empty:
        return 0
    cutoff = pd.Timestamp(today - timedelta(days=FCF_WINDOW_DAYS))
    return int((q["period_end"] >= cutoff).sum())


def audit(conn, today: date, merges: Mapping[int, Sequence[int]] | None = None,
          tickers: set[str] | None = None) -> list[TickerAudit]:
    rows = conn.execute(
        "SELECT ticker, cik, sector_etf, industry_etf FROM tickers ORDER BY ticker").fetchall()
    out = []
    for ticker, cik, sector_etf, industry_etf in rows:
        if tickers is not None and ticker not in tickers:
            continue
        a = TickerAudit(ticker, cik, sector_etf=sector_etf, industry_etf=industry_etf)
        first, last = conn.execute(
            "SELECT MIN(date), MAX(date) FROM prices WHERE symbol = ?", (ticker,)).fetchone()
        a.price_first = date.fromisoformat(first) if first else None
        a.price_last = date.fromisoformat(last) if last else None
        if cik is not None:
            revenue = _quarters(conn, cik, "revenue", merges)
            a.revenue_quarters = len(revenue)
            if not revenue.empty:
                a.first_revenue_quarter = revenue["period_end"].min().date()
            ocf = _quarters(conn, cik, "operating_cash_flow", merges)
            capex = _quarters(conn, cik, "capex", merges)
            a.has_fcf = min(_recent_count(ocf, today), _recent_count(capex, today)) >= 4
            latest = conn.execute(
                "SELECT accession FROM filings WHERE cik = ? AND form IN ('10-Q', '10-K') "
                "ORDER BY filing_date DESC LIMIT 1", (cik,)).fetchone()
            if latest:
                has = conn.execute("SELECT 1 FROM facts WHERE cik = ? AND accession = ? LIMIT 1",
                                   (cik, latest[0])).fetchone()
                a.facts_behind_filing = has is None
        a.flags = _flags(a, today)
        out.append(a)
    return out


def _flags(a: TickerAudit, today: date) -> list[str]:
    flags = []
    if a.cik is None:
        flags.append("no CIK")
    if a.price_last is None:
        flags.append("no prices")
    elif (today - a.price_last).days > STALE_PRICE_DAYS:
        flags.append("stale prices")
    if a.cik is not None and a.revenue_quarters == 0:
        flags.append("no fundamentals")
    if (a.price_first and a.price_first < date(2012, 1, 1) and a.first_revenue_quarter
            and a.first_revenue_quarter.year >= a.price_first.year + 3):
        flags.append("check CIK merge")
    if a.facts_behind_filing:
        flags.append("companyfacts behind latest 10-Q/10-K")
    if a.cik is not None and a.revenue_quarters and not a.has_fcf:
        flags.append("no FCF")
    if a.sector_etf is None:
        flags.append("no sector ETF")
    return flags


def summarize(audits: list[TickerAudit]) -> dict[str, int]:
    return {
        "tickers": len(audits),
        "with prices": sum(a.price_last is not None for a in audits),
        "with 8+ revenue quarters": sum(a.revenue_quarters >= 8 for a in audits),
        "with FCF": sum(a.has_fcf for a in audits),
        "companyfacts behind": sum(a.facts_behind_filing for a in audits),
        "flagged": sum(bool(a.flags) for a in audits),
    }
