"""Insider transaction history from SEC's quarterly Form 3/4/5 datasets."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from analyzer.data.edgar.form4 import INSIDER_COLUMNS, sync_form4_filings
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import get_state, set_state

BULK_URL = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
            "{label}_form345.zip")
STATE_QUARTERS = "insider_bulk_quarters"
STATE_CIKS = "insider_bulk_ciks"
REQUIRED_FILES = ("SUBMISSION.TSV", "REPORTINGOWNER.TSV", "NONDERIV_TRANS.TSV")
log = logging.getLogger(__name__)


def _iso(text: str) -> str | None:
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _float(text: str) -> float | None:
    try:
        return float(text) if str(text).strip() else None
    except ValueError:
        return None


def _member(z: zipfile.ZipFile, name: str) -> str | None:
    return next((n for n in z.namelist() if n.split("/")[-1].upper() == name), None)


def _validate_zip(data: bytes) -> None:
    """Reject a body that is not a bulk dataset zip, so it is never cached."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            missing = [name for name in REQUIRED_FILES if _member(z, name) is None]
    except zipfile.BadZipFile as exc:  # not a ValueError, which is what the client expects
        raise ValueError("not a zip archive") from exc
    if missing:
        raise ValueError(f"zip lacks {', '.join(missing)}")


def _fetch_zip(sec: HttpClient, label: str) -> bytes:
    return sec.get_bytes(BULK_URL.format(label=label), cache_key=f"sec/insider/{label}.zip",
                         validate=_validate_zip)


def _read(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    return pd.read_csv(z.open(_member(z, name)), sep="\t", dtype=str, keep_default_na=False,
                       quoting=3)


def parse_insider_zip(data: bytes, issuer_ciks: set[int]) -> list[tuple]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sub = _read(z, "SUBMISSION.TSV")
        owners = _read(z, "REPORTINGOWNER.TSV")
        trans = _read(z, "NONDERIV_TRANS.TSV")
    sub = sub[sub["DOCUMENT_TYPE"].isin(["4", "4/A"])].copy()
    sub["ISSUERCIK"] = pd.to_numeric(sub["ISSUERCIK"], errors="coerce")
    sub = sub[sub["ISSUERCIK"].isin(issuer_ciks)].copy()
    sub["ISSUERCIK"] = sub["ISSUERCIK"].astype("int64")
    sub["FILING_DATE"] = sub["FILING_DATE"].map(_iso)
    undated = sub["FILING_DATE"].isna()  # filing_date is NOT NULL in the table
    if undated.any():
        log.warning("skipping %d insider filings with an unreadable filing date", undated.sum())
        sub = sub[~undated]
    if sub.empty:
        return []
    owners = owners[owners["ACCESSION_NUMBER"].isin(sub["ACCESSION_NUMBER"])]
    by_owner = owners.groupby("ACCESSION_NUMBER").agg(
        owner_names=("RPTOWNERNAME", lambda s: "; ".join(x for x in s if x) or None),
        relationship=("RPTOWNER_RELATIONSHIP", lambda s: ",".join(
            sorted({p for x in s for p in x.split(",") if p})) or None),
        officer_title=("RPTOWNER_TITLE", lambda s: "; ".join(x for x in s if x) or None),
    )
    m = trans.merge(sub[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK"]], on="ACCESSION_NUMBER")
    m = m.merge(by_owner, left_on="ACCESSION_NUMBER", right_index=True, how="left")
    m["seq"] = m.groupby("ACCESSION_NUMBER").cumcount()
    return [
        (r.ACCESSION_NUMBER, int(r.seq), int(r.ISSUERCIK), r.FILING_DATE,
         r.owner_names if isinstance(r.owner_names, str) else None,
         r.relationship if isinstance(r.relationship, str) else None,
         r.officer_title if isinstance(r.officer_title, str) else None,
         _iso(r.TRANS_DATE), r.TRANS_CODE or None, _float(r.TRANS_SHARES),
         _float(r.TRANS_PRICEPERSHARE), r.TRANS_ACQUIRED_DISP_CD or None, "bulk")
        for r in m.itertuples()
    ]


def quarters_to_load(today: date, start: tuple[int, int] = (2013, 1)) -> list[str]:
    current = (today.month - 1) // 3 + 1
    last = (today.year, current - 1) if current > 1 else (today.year - 1, 4)
    y, q = start
    out = []
    while (y, q) <= last:
        out.append(f"{y}q{q}")
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def quarter_end(label: str) -> date:
    y, q = int(label[:4]), int(label[-1])
    first_of_next = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
    return first_of_next - timedelta(days=1)


def _insert(conn, rows: list[tuple]) -> None:
    accessions = sorted({r[0] for r in rows})
    with conn:
        conn.executemany("DELETE FROM insider_transactions WHERE accession = ?",
                         [(a,) for a in accessions])
        conn.executemany(
            f"INSERT INTO insider_transactions ({', '.join(INSIDER_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(INSIDER_COLUMNS))})", rows)


@dataclass
class InsiderSync:
    loaded: list[str] = field(default_factory=list)
    backfilled_ciks: int = 0
    xml_filings: int = 0


def sync_insider(conn, sec: HttpClient, ciks: set[int], today: date) -> InsiderSync:
    """Load every published quarter, then fill the unpublished tail from Form 4 XML.

    Bookkeeping, saved after each step so a failure part-way loses nothing:
    `loaded` is the set of quarters in the table; `covered` is the set of CIKs that have been
    read from every one of those quarters. Inserts are delete-then-insert per accession, so
    re-reading a quarter is harmless.
    """
    result = InsiderSync()
    loaded = set(json.loads(get_state(conn, STATE_QUARTERS) or "[]"))
    covered = set(json.loads(get_state(conn, STATE_CIKS) or "[]"))

    new_ciks = ciks - covered
    if new_ciks:  # re-read cached quarters for companies added since
        for label in sorted(loaded):
            rows = parse_insider_zip(_fetch_zip(sec, label), new_ciks)
            if rows:
                _insert(conn, rows)
        if loaded:
            result.backfilled_ciks = len(new_ciks)
        covered |= new_ciks
        set_state(conn, STATE_CIKS, json.dumps(sorted(covered)))

    for label in quarters_to_load(today):
        if label in loaded:
            continue
        try:
            data = _fetch_zip(sec, label)
        except NotFound:
            break  # not published yet; later quarters aren't either
        rows = parse_insider_zip(data, ciks)
        if rows:
            _insert(conn, rows)
        loaded.add(label)
        covered &= ciks  # this quarter was read for `ciks` only; others are no longer covered
        set_state(conn, STATE_QUARTERS, json.dumps(sorted(loaded)))
        set_state(conn, STATE_CIKS, json.dumps(sorted(covered)))
        result.loaded.append(label)

    since = quarter_end(max(loaded)) if loaded else today - timedelta(days=120)
    result.xml_filings = sync_form4_filings(conn, sec, ciks, since)
    return result
