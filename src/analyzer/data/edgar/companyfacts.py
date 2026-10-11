"""Pull the engine's concepts out of SEC companyfacts JSON."""

from __future__ import annotations

import logging
from datetime import timedelta

from analyzer.data.edgar.tags import CONCEPTS, PERIODIC_FORMS, Concept
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FALLBACK_TOLERANCE = 0.005  # a fallback sum within 0.5% (plus $1 rounding) of the total agrees
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


def _tag_rows(facts: dict, concept: Concept, qualified: str, cik: int) -> list[tuple]:
    namespace, tag = qualified.split(":")
    entry = facts.get(namespace, {}).get(tag)
    if not entry:
        return []
    rows = []
    for f in entry.get("units", {}).get(concept.unit, []):
        if f.get("form") not in PERIODIC_FORMS:
            continue
        if (concept.kind == "duration") != ("start" in f):
            continue
        rows.append((cik, concept.name, qualified, concept.unit, f.get("start", ""), f["end"],
                     float(f["val"]), f["filed"], f["form"], f["accn"], "companyfacts"))
    return rows


def _agrees(value: float, totals: set[float]) -> bool:
    return any(abs(value - t) <= FALLBACK_TOLERANCE * abs(t) + 1 for t in totals)


def _fallback_rows(facts: dict, concept: Concept, cik: int, primary: list[tuple]) -> list[tuple]:
    """One summed row per (period, filing) with fallback tags and no primary tag; none at all if
    any such sum disagrees with a primary value of the same period from another filing (usually
    a later report's comparative). Fallback lines next to the total in the same filing are a
    breakdown of it and are neither used nor compared."""
    groups: dict[tuple, dict[str, tuple]] = {}
    for qualified in concept.fallback_tags:
        for r in _tag_rows(facts, concept, qualified, cik):
            groups.setdefault((r[4], r[5], r[9]), {}).setdefault(qualified, r)
    totals: dict[tuple, set[float]] = {}
    for r in primary:
        totals.setdefault((r[4], r[5]), set()).add(r[6])
    covered = {(r[4], r[5], r[9]) for r in primary}
    out = []
    for key, parts in groups.items():
        if key in covered:
            continue
        start, end, _accn = key
        first = next(iter(parts.values()))
        value = sum(r[6] for r in parts.values())
        if (start, end) in totals and not _agrees(value, totals[(start, end)]):
            log.debug("CIK %d: %s fallback %s disagrees with its total for %s..%s; not used",
                      cik, concept.name, "+".join(parts), start, end)
            return []
        out.append((*first[:2], "+".join(parts), *first[3:6], value,
                    max(r[7] for r in parts.values()), *first[8:]))
    return out


def extract_facts(cf: dict, cik: int) -> list[tuple]:
    rows = []
    facts = cf.get("facts", {})
    for concept in CONCEPTS:
        concept_rows = [r for qualified in concept.tags
                        for r in _tag_rows(facts, concept, qualified, cik)]
        if concept.fallback_tags:
            concept_rows += _fallback_rows(facts, concept, cik, concept_rows)
        if concept.name == "shares_outstanding":
            concept_rows = _sum_share_classes(concept_rows)
        rows.extend(concept_rows)
    return rows


def store_facts(conn, rows: list[tuple]) -> None:
    upsert_rows(conn, "facts", FACT_COLUMNS, rows)


def replace_company_facts(conn, cik: int, rows: list[tuple]) -> None:
    """Store a fresh extraction of the company's companyfacts in place of the previous one, so a
    row the extraction no longer makes (a dropped fallback, a removed tag) does not linger.
    Rows read from filing instances are kept."""
    with conn:
        conn.execute("DELETE FROM facts WHERE cik = ? AND source = 'companyfacts'", (cik,))
        conn.executemany(
            f"INSERT OR REPLACE INTO facts ({', '.join(FACT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(FACT_COLUMNS))})", rows)
