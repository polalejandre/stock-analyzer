"""Pull the engine's concepts out of SEC companyfacts JSON."""

from __future__ import annotations

import logging
from datetime import timedelta

from analyzer.data.edgar.tags import CONCEPTS, PERIODIC_FORMS
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

log = logging.getLogger(__name__)

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


def _sum_share_classes(rows: list[tuple]) -> list[tuple]:
    """Collapse per-class cover-page share counts of one filing into one total.

    A dual-class issuer reports one `dei:EntityCommonStockSharesOutstanding` value per class with
    the same end date and accession; the facts primary key would keep an arbitrary one. Exact
    duplicate values are dropped first, the distinct values are summed.
    """
    groups: dict[tuple, list[tuple]] = {}
    for row in rows:  # key: (tag, period_start, period_end, accession)
        groups.setdefault((row[2], row[4], row[5], row[9]), []).append(row)
    out = []
    for key, group in groups.items():
        values = sorted({r[6] for r in group})
        if len(values) > 1:
            log.debug("summed %d share-class counts for %s end=%s accn=%s: %s",
                      len(values), key[0], key[2], key[3], values)
        first = group[0]
        out.append((*first[:6], sum(values), max(r[7] for r in group), *first[8:]))
    return out


def extract_facts(cf: dict, cik: int) -> list[tuple]:
    rows = []
    facts = cf.get("facts", {})
    for concept in CONCEPTS:
        concept_rows = []
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
                concept_rows.append((cik, concept.name, qualified, concept.unit,
                                     f.get("start", ""), f["end"], float(f["val"]), f["filed"],
                                     f["form"], f["accn"], "companyfacts"))
        if concept.name == "shares_outstanding":
            concept_rows = _sum_share_classes(concept_rows)
        rows.extend(concept_rows)
    return rows


def store_facts(conn, rows: list[tuple]) -> None:
    upsert_rows(conn, "facts", FACT_COLUMNS, rows)
