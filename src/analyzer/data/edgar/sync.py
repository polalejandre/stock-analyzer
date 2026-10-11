"""Refresh one company's submissions and, optionally, its companyfacts."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from analyzer.data.edgar.companies import fetch_submissions, store_submissions
from analyzer.data.edgar.companyfacts import (
    extract_facts,
    fetch_companyfacts,
    replace_company_facts,
)
from analyzer.data.edgar.tags import TAG_MAP_FINGERPRINT
from analyzer.data.http import HttpClient
from analyzer.store.db import get_state, set_state

log = logging.getLogger(__name__)

TAG_MAP_KEY = "facts_tag_map"  # sync_state: fingerprint of the tag map the stored facts follow


def refresh_company(conn, sec: HttpClient, cik: int, *, max_age: timedelta | None,
                    facts: bool = True, now: datetime | None = None) -> None:
    """Store the company's submissions and (if `facts`) its companyfacts.

    `max_age` is how old a cached SEC response may be before it is refetched: `timedelta(0)`
    always refetches; `None` reuses any cached copy forever.
    """
    now = now or datetime.now(UTC)
    store_submissions(conn, fetch_submissions(sec, cik, max_age=max_age), now)
    if facts:
        cf = fetch_companyfacts(sec, cik, max_age=max_age)
        if cf:
            replace_company_facts(conn, cik, extract_facts(cf, cik))


def reextract_facts_if_tags_changed(conn, sec: HttpClient, ciks: Iterable[int]) -> int:
    """After a change to the tag map, extract every company's facts again from its cached
    companyfacts (fetched only when not cached). Returns how many companies were re-extracted."""
    if get_state(conn, TAG_MAP_KEY) == TAG_MAP_FINGERPRINT:
        return 0
    done, failed = 0, 0
    for cik in ciks:
        try:
            cf = fetch_companyfacts(sec, cik, max_age=None)
            if cf:
                replace_company_facts(conn, cik, extract_facts(cf, cik))
                done += 1
        except Exception:  # one unreadable company must not block the step; repeated next run
            log.exception("re-extracting facts for CIK %d failed", cik)
            failed += 1
    if not failed:
        set_state(conn, TAG_MAP_KEY, TAG_MAP_FINGERPRINT)
    return done
