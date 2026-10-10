"""EDGAR daily master index: which companies filed what, day by day."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import get_state, set_state

MASTER_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/master.{ymd}.idx"
POINTER_KEY = "edgar_daily_index_done"
HOLIDAY_AFTER_DAYS = 3


@dataclass(frozen=True)
class IndexEntry:
    cik: int
    company: str
    form: str
    date_filed: date
    accession: str


def _parse_date(text: str) -> date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {text!r}")


def parse_master_index(text: str) -> list[IndexEntry]:
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.startswith("-----")), None)
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        cik, company, form, filed, filename = parts
        accession = filename.strip().rsplit("/", 1)[-1].removesuffix(".txt")
        out.append(IndexEntry(int(cik), company.strip(), form.strip(), _parse_date(filed), accession))
    return out


def fetch_master_index(sec: HttpClient, d: date) -> list[IndexEntry] | None:
    url = MASTER_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
    try:
        text = sec.get_text(url, cache_key=f"sec/daily-index/{d:%Y%m%d}.idx")
    except NotFound:
        return None
    return parse_master_index(text)


def next_pointer(last_done: date, published: Mapping[date, bool], today: date) -> date:
    pointer = last_done
    for d in sorted(published):
        if published[d] or d.weekday() >= 5 or (today - d).days > HOLIDAY_AFTER_DAYS:
            pointer = d
        else:
            break
    return pointer


def scan_daily_indexes(conn, sec: HttpClient, ciks: set[int], today: date) -> dict[int, set[str]]:
    last = get_state(conn, POINTER_KEY)
    if last is None:
        set_state(conn, POINTER_KEY, (today - timedelta(days=1)).isoformat())
        return {}
    last_done = date.fromisoformat(last)
    changed: dict[int, set[str]] = {}
    published: dict[date, bool] = {}
    d = last_done + timedelta(days=1)
    while d < today:
        if d.weekday() >= 5:
            published[d] = False
        else:
            entries = fetch_master_index(sec, d)
            published[d] = entries is not None
            for e in entries or []:
                if e.cik in ciks:
                    changed.setdefault(e.cik, set()).add(e.form)
        d += timedelta(days=1)
    set_state(conn, POINTER_KEY, next_pointer(last_done, published, today).isoformat())
    return changed
