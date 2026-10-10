"""Insider transactions from individual Form 4 XML documents."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

INSIDER_COLUMNS = ["accession", "seq", "issuer_cik", "filing_date", "owner_names", "relationship",
                   "officer_title", "trans_date", "trans_code", "shares", "price",
                   "acquired_disposed", "source"]
_FLAGS = (("isDirector", "Director"), ("isOfficer", "Officer"),
          ("isTenPercentOwner", "TenPercentOwner"), ("isOther", "Other"))


def form4_document_url(cik: int, accession: str, primary_document: str) -> str:
    doc = re.sub(r"^xsl[^/]+/", "", primary_document)  # raw XML, not the XSL-rendered view
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def _text(el, path: str) -> str | None:
    value = (el.findtext(path) or "").strip()
    return value or None


def _float(el, path: str) -> float | None:
    value = _text(el, path)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def parse_form4(xml: bytes, accession: str, filing_date: str) -> list[tuple]:
    root = ET.fromstring(xml)
    issuer = int(root.findtext("issuer/issuerCik").strip())
    names, relationships, titles = [], set(), []
    for owner in root.findall("reportingOwner"):
        name = _text(owner, "reportingOwnerId/rptOwnerName")
        if name:
            names.append(name)
        rel = owner.find("reportingOwnerRelationship")
        if rel is None:
            continue
        for tag, label in _FLAGS:
            if (rel.findtext(tag) or "").strip().lower() in ("1", "true"):
                relationships.add(label)
        title = _text(rel, "officerTitle")
        if title:
            titles.append(title)
    rows = []
    for seq, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        trans_date = _text(t, "transactionDate/value")
        rows.append((
            accession, seq, issuer, filing_date, "; ".join(names) or None,
            ",".join(sorted(relationships)) or None, "; ".join(titles) or None,
            trans_date[:10] if trans_date else None,
            _text(t, "transactionCoding/transactionCode"),
            _float(t, ".//transactionShares/value"),
            _float(t, ".//transactionPricePerShare/value"),
            _text(t, ".//transactionAcquiredDisposedCode/value"),
            "xml",
        ))
    return rows


def sync_form4_filings(conn, sec: HttpClient, ciks: set[int], since: date) -> int:
    pending = conn.execute(
        "SELECT f.accession, f.cik, f.filing_date, f.primary_document FROM filings f "
        "WHERE f.form IN ('4', '4/A') AND f.filing_date > ? "
        "AND NOT EXISTS (SELECT 1 FROM insider_transactions t WHERE t.accession = f.accession)",
        (since.isoformat(),),
    ).fetchall()
    parsed = 0
    for accession, cik, filing_date, document in pending:
        if cik not in ciks or not document:
            continue
        try:
            xml = sec.get_bytes(form4_document_url(cik, accession, document),
                                cache_key=f"sec/form4/{accession}.xml")
        except NotFound:
            continue
        upsert_rows(conn, "insider_transactions", INSIDER_COLUMNS,
                    parse_form4(xml, accession, filing_date))
        parsed += 1
    return parsed
