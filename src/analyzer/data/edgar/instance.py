"""Read facts straight from a filing's XBRL instance when companyfacts lags behind."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from analyzer.data.edgar.companyfacts import store_facts
from analyzer.data.edgar.tags import CONCEPT_BY_TAG
from analyzer.data.http import FetchError, HttpClient, NotFound

log = logging.getLogger(__name__)

FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_LINKBASE = re.compile(r"(_cal|_def|_lab|_pre)\.xml$|FilingSummary\.xml$", re.IGNORECASE)


def _validate_xml(body: bytes) -> None:
    try:
        ET.fromstring(body)
    except ET.ParseError as e:
        raise ValueError(f"invalid XML: {e}") from e


def find_instance_name(index_json: dict) -> str | None:
    names = [item["name"] for item in index_json["directory"]["item"]]
    for n in names:
        if n.endswith("_htm.xml"):  # instance extracted from inline XBRL
            return n
    for n in names:
        if n.endswith(".xml") and not _LINKBASE.search(n):
            return n
    return None


def _namespace_prefix(uri: str) -> str | None:
    if "fasb.org/us-gaap" in uri:
        return "us-gaap"
    if "xbrl.sec.gov/dei" in uri:
        return "dei"
    return None


def parse_instance(xml: bytes, *, cik: int, accession: str, form: str, filed: str) -> list[tuple]:
    root = ET.fromstring(xml)
    contexts: dict[str, tuple[str, str]] = {}
    for ctx in root.iter(f"{XBRLI}context"):
        if ctx.find(f"{XBRLI}entity/{XBRLI}segment") is not None or ctx.find(f"{XBRLI}scenario") is not None:
            continue
        period = ctx.find(f"{XBRLI}period")
        instant = period.findtext(f"{XBRLI}instant")
        if instant:
            contexts[ctx.get("id")] = ("", instant.strip())
        else:
            contexts[ctx.get("id")] = (period.findtext(f"{XBRLI}startDate").strip(),
                                       period.findtext(f"{XBRLI}endDate").strip())
    units: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        measures = [m.text.strip().split(":")[-1] for m in unit.iter(f"{XBRLI}measure")]
        units[unit.get("id")] = "/".join(measures)  # USD, shares, or USD/shares

    rows, seen = [], set()
    for el in root:
        if not el.tag.startswith("{"):
            continue
        uri, local = el.tag[1:].split("}")
        prefix = _namespace_prefix(uri)
        concept = CONCEPT_BY_TAG.get(f"{prefix}:{local}") if prefix else None
        ctx = contexts.get(el.get("contextRef"))
        if concept is None or ctx is None or units.get(el.get("unitRef")) != concept.unit:
            continue
        start, end = ctx
        text = (el.text or "").strip()
        if (concept.kind == "duration") != bool(start) or el.get(XSI_NIL) == "true" or not text:
            continue
        key = (concept.name, f"{prefix}:{local}", start, end)
        if key in seen:
            continue
        seen.add(key)
        try:
            value = float(text)
        except ValueError:
            continue
        rows.append((cik, concept.name, f"{prefix}:{local}", concept.unit, start, end, value,
                     filed, form, accession, "instance"))
    return rows


def fetch_instance_facts(sec: HttpClient, cik: int, accession: str, form: str,
                         filed: str) -> list[tuple]:
    acc = accession.replace("-", "")
    index = sec.get_json(FILING_INDEX_URL.format(cik=cik, acc=acc),
                         cache_key=f"sec/filings/{acc}/index.json")
    name = find_instance_name(index)
    if name is None:
        return []
    xml = sec.get_bytes(FILE_URL.format(cik=cik, acc=acc, name=name),
                        cache_key=f"sec/filings/{acc}/{name}", validate=_validate_xml)
    return parse_instance(xml, cik=cik, accession=accession, form=form, filed=filed)


def apply_instance_fallback(conn, sec: HttpClient, cik: int, today: date,
                            lookback_days: int = 120) -> list[str]:
    pending = conn.execute(
        "SELECT accession, form, filing_date FROM filings f WHERE cik = ? "
        "AND form IN ('10-Q', '10-K') AND filing_date >= ? "
        "AND NOT EXISTS (SELECT 1 FROM facts x WHERE x.cik = f.cik AND x.accession = f.accession) "
        "ORDER BY filing_date DESC LIMIT 2",
        (cik, (today - timedelta(days=lookback_days)).isoformat()),
    ).fetchall()
    filled = []
    for accession, form, filed in pending:
        try:
            rows = fetch_instance_facts(sec, cik, accession, form, filed)
        except (FetchError, NotFound, ValueError, KeyError, AttributeError) as e:
            log.warning("instance fallback failed for %s: %s", accession, e)
            continue
        if rows:
            store_facts(conn, rows)
            filled.append(accession)
    return filled
