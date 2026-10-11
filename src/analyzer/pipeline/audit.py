"""`analyzer audit`: per-ticker data coverage and the problems worth a look."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.edgar.facts_read import load_quarters

STALE_PRICE_DAYS = 4
FCF_WINDOW_DAYS = 470  # four quarters plus filing lag
MERGE_CHECK_PRICES_BEFORE = date(2012, 1, 1)
MERGE_CHECK_GAP_YEARS = 3
FCF_EXPECTED_ABSENT = "no FCF (financials, expected)"
PROGRESS_EVERY = 250


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
    gics_sector: str | None = None
    active: bool = True
    flags: list[str] = field(default_factory=list)


def _quarters(conn, cik, concept, merges) -> pd.DataFrame:
    return load_quarters(conn, cik, concept, merges=merges)


def _recent_count(q: pd.DataFrame, today: date) -> int:
    if q.empty:
        return 0
    cutoff = pd.Timestamp(today - timedelta(days=FCF_WINDOW_DAYS))
    return int((q["period_end"] >= cutoff).sum())


def audit(conn, today: date, merges: Mapping[int, Sequence[int]] | None = None,
          tickers: set[str] | None = None, *, active: set[str] | None = None,
          progress: Callable[[int, int], None] | None = None) -> list[TickerAudit]:
    rows = conn.execute(
        "SELECT ticker, cik, sector_etf, industry_etf, gics_sector FROM tickers "
        "ORDER BY ticker").fetchall()
    rows = [r for r in rows if tickers is None or r[0] in tickers]
    out = []
    for n, (ticker, cik, sector_etf, industry_etf, gics_sector) in enumerate(rows, 1):
        a = TickerAudit(ticker, cik, sector_etf=sector_etf, industry_etf=industry_etf,
                        gics_sector=gics_sector, active=active is None or ticker in active)
        try:
            _fill(conn, a, today, merges)
            a.flags = _flags(a, today)
        except Exception as exc:  # one bad ticker must not sink the report
            a.flags = [f"audit error: {type(exc).__name__}: {exc}"]
        out.append(a)
        if progress and n % PROGRESS_EVERY == 0:
            progress(n, len(rows))
    return out


def _fill(conn, a: TickerAudit, today: date, merges) -> None:
    cik = a.cik
    first, last = conn.execute(
        "SELECT MIN(date), MAX(date) FROM prices WHERE symbol = ?", (a.ticker,)).fetchone()
    a.price_first = date.fromisoformat(first) if first else None
    a.price_last = date.fromisoformat(last) if last else None
    if cik is None:
        return
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


def _flags(a: TickerAudit, today: date) -> list[str]:
    flags = []
    if a.active:
        if a.cik is None:
            flags.append("no CIK")
        if a.price_last is None:
            flags.append("no prices")
        elif (today - a.price_last).days > STALE_PRICE_DAYS:
            flags.append("stale prices")
    if a.cik is not None and a.revenue_quarters == 0:
        flags.append("no fundamentals")
    if (a.price_first and a.price_first < MERGE_CHECK_PRICES_BEFORE and a.first_revenue_quarter
            and a.first_revenue_quarter.year >= a.price_first.year + MERGE_CHECK_GAP_YEARS):
        flags.append("check CIK merge")
    if a.active and a.facts_behind_filing:
        flags.append("companyfacts behind latest 10-Q/10-K")
    if a.cik is not None and a.revenue_quarters and not a.has_fcf:
        flags.append(FCF_EXPECTED_ABSENT if a.gics_sector == "Financials" else "no FCF")
    if a.sector_etf is None:
        flags.append("no sector ETF")
    return flags


def actionable(a: TickerAudit) -> bool:
    """Active ticker with at least one flag that is not an expected absence."""
    return a.active and any(f != FCF_EXPECTED_ABSENT for f in a.flags)


def missing_reference_etfs(conn) -> list[str]:
    """Reference ETFs named in `tickers` that have no price rows."""
    return [r[0] for r in conn.execute(
        "SELECT sym FROM (SELECT sector_etf AS sym FROM tickers UNION "
        "SELECT industry_etf FROM tickers) WHERE sym IS NOT NULL AND sym NOT IN "
        "(SELECT DISTINCT symbol FROM prices) ORDER BY sym")]


def summarize(audits: list[TickerAudit], missing_etfs: Sequence[str] = ()) -> dict[str, int]:
    n_active = sum(a.active for a in audits)
    return {
        "tickers": len(audits),
        "active tickers": n_active,
        "inactive tickers": len(audits) - n_active,
        "with prices": sum(a.price_last is not None for a in audits),
        "with 8+ revenue quarters": sum(a.revenue_quarters >= 8 for a in audits),
        "with FCF": sum(a.has_fcf for a in audits),
        "companyfacts behind": sum(a.facts_behind_filing for a in audits),
        "flagged": sum(actionable(a) for a in audits),
        "reference ETFs without prices": len(missing_etfs),
    }
