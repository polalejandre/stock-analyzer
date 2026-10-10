"""Pull the engine's concepts out of SEC companyfacts JSON."""

from __future__ import annotations

from datetime import timedelta

from analyzer.data.edgar.tags import CONCEPTS, PERIODIC_FORMS
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FACT_COLUMNS = ["cik", "concept", "tag", "unit", "period_start", "period_end", "value", "filed",
                "form", "accession", "source"]


def fetch_companyfacts(sec: HttpClient, cik: int,
                       max_age: timedelta | None = timedelta(hours=20)) -> dict | None:
    try:
        return sec.get_json(COMPANYFACTS_URL.format(cik=cik),
                            cache_key=f"sec/companyfacts/{cik}.json", max_age=max_age)
    except NotFound:
        return None  # no XBRL facts for this company


def extract_facts(cf: dict, cik: int) -> list[tuple]:
    rows = []
    facts = cf.get("facts", {})
    for concept in CONCEPTS:
        for qualified in concept.tags:
            namespace, tag = qualified.split(":")
            entry = facts.get(namespace, {}).get(tag)
            if not entry:
                continue
            for f in entry.get("units", {}).get(concept.unit, []):
                if f.get("form") not in PERIODIC_FORMS:
                    continue
                if (concept.kind == "duration") != ("start" in f):
                    continue
                rows.append((cik, concept.name, qualified, concept.unit, f.get("start", ""),
                             f["end"], float(f["val"]), f["filed"], f["form"], f["accn"],
                             "companyfacts"))
    return rows


def store_facts(conn, rows: list[tuple]) -> None:
    upsert_rows(conn, "facts", FACT_COLUMNS, rows)
