"""Refresh one company's submissions and, optionally, its companyfacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analyzer.data.edgar.companies import fetch_submissions, store_submissions
from analyzer.data.edgar.companyfacts import extract_facts, fetch_companyfacts, store_facts
from analyzer.data.http import HttpClient


def refresh_company(conn, sec: HttpClient, cik: int, *, max_age: timedelta | None,
                    facts: bool = True, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    store_submissions(conn, fetch_submissions(sec, cik, max_age=max_age), now)
    if facts:
        cf = fetch_companyfacts(sec, cik, max_age=max_age)
        if cf:
            store_facts(conn, extract_facts(cf, cik))
