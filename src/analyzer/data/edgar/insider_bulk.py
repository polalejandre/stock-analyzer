"""Insider transaction history from SEC's quarterly Form 3/4/5 datasets."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from analyzer.data.edgar.form4 import INSIDER_COLUMNS, sync_form4_filings
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import get_state

BULK_URL = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
            "{label}_form345.zip")
STATE_QUARTERS = "insider_bulk_quarters"
STATE_CIKS = "insider_bulk_ciks"
REQUIRED_FILES = ("SUBMISSION.TSV", "REPORTINGOWNER.TSV", "NONDERIV_TRANS.TSV")
MAX_SKIPPED_SHARE = 0.05  # more unreadable filings than this means the file format is not what we expect
MIN_SKIPPED_TO_RAISE = 3  # ...but only once that many are skipped; a lone bad row is just skipped
LATE_QUARTER_DAYS = 180   # a quarter missing this long after it ended is probably a wrong URL
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
                       quoting=csv.QUOTE_NONE)


def _tolerate_unreadable(bad: pd.Series, total: int, column: str) -> None:
    """Let a few unreadable values be skipped; many of them means the format changed.

    Raises only when at least MIN_SKIPPED_TO_RAISE are bad AND they exceed MAX_SKIPPED_SHARE of
    `total`. A real format mismatch hits every filing, whereas one bad row among a handful (a lone
    new CIK) must not block the quarter. Skipping everything would load a quarter with zero rows
    and mark it done forever.
    """
    if bad.empty:
        return
    if len(bad) >= MIN_SKIPPED_TO_RAISE and len(bad) / total > MAX_SKIPPED_SHARE:
        raise ValueError(f"{len(bad)} of {total} Form 4 filings have an unreadable {column} "
                         f"(first bad value: {bad.iloc[0]!r})")
    log.warning("skipping %d of %d Form 4 filings with an unreadable %s (first: %r)",
                len(bad), total, column, bad.iloc[0])


def parse_insider_zip(data: bytes, issuer_ciks: set[int]) -> list[tuple]:
    """Rows for the Form 4 / 4/A filings of `issuer_ciks`.

    Raises ValueError when at least 3 and more than 5% of the relevant filings have an unreadable
    ISSUERCIK or FILING_DATE (the columns are unprobed; a format change must not look like an
    empty quarter).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sub = _read(z, "SUBMISSION.TSV")
        owners = _read(z, "REPORTINGOWNER.TSV")
        trans = _read(z, "NONDERIV_TRANS.TSV")
    sub = sub.drop_duplicates("ACCESSION_NUMBER")  # a repeated row would double its transactions
    sub = sub[sub["DOCUMENT_TYPE"].isin(["4", "4/A"])]
    cik = pd.to_numeric(sub["ISSUERCIK"], errors="coerce")
    _tolerate_unreadable(sub.loc[cik.isna(), "ISSUERCIK"], len(sub), "ISSUERCIK")
    sub = sub.assign(ISSUERCIK=cik)
    sub = sub[sub["ISSUERCIK"].isin(issuer_ciks)].copy()
    sub["ISSUERCIK"] = sub["ISSUERCIK"].astype("int64")
    filed = sub["FILING_DATE"].map(_iso)
    undated = filed.isna()  # filing_date is NOT NULL in the table
    _tolerate_unreadable(sub.loc[undated, "FILING_DATE"], len(sub), "FILING_DATE")
    sub = sub.assign(FILING_DATE=filed)[~undated]
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


def _save_state(conn, loaded: set[str], covered: set[int]) -> None:
    """Write both bookkeeping keys in ONE transaction.

    Two separate commits could crash in between and leave a quarter marked loaded while `covered`
    still lists a CIK that quarter was read without; that CIK would never be backfilled.
    """
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
            [(STATE_QUARTERS, json.dumps(sorted(loaded))), (STATE_CIKS, json.dumps(sorted(covered)))])


def _parse(data: bytes, label: str, ciks: set[int]) -> list[tuple]:
    try:
        return parse_insider_zip(data, ciks)
    except ValueError as exc:
        raise ValueError(f"insider quarter {label}: {exc}") from exc


def sync_insider(conn, sec: HttpClient, ciks: set[int], today: date) -> InsiderSync:
    """Load every published quarter, then fill the unpublished tail from Form 4 XML.

    Bookkeeping: `loaded` is the set of quarters in the table; `covered` is the set of CIKs that
    have been read from EVERY one of those quarters. Both are saved together after each quarter,
    so a failure part-way loses nothing. Inserts are delete-then-insert per accession, so
    re-reading a quarter is harmless. A quarter that fails to fetch or parse is not marked loaded
    and its error propagates.
    """
    result = InsiderSync()
    loaded = set(json.loads(get_state(conn, STATE_QUARTERS) or "[]"))
    covered = set(json.loads(get_state(conn, STATE_CIKS) or "[]"))

    new_ciks = ciks - covered
    if new_ciks:  # re-read cached quarters for companies added since
        complete = True
        for label in sorted(loaded):
            try:
                data = _fetch_zip(sec, label)
            except NotFound:
                log.warning("insider quarter %s is no longer cached or downloadable; "
                            "%d new CIKs will be backfilled on a later run", label, len(new_ciks))
                complete = False
                continue
            rows = _parse(data, label, new_ciks)
            if rows:
                _insert(conn, rows)
        if complete:
            covered |= new_ciks
            if loaded:  # with nothing loaded yet the first quarter below records the CIKs
                result.backfilled_ciks = len(new_ciks)
                _save_state(conn, loaded, covered)

    for label in quarters_to_load(today):
        if label in loaded:
            continue
        try:
            data = _fetch_zip(sec, label)
        except NotFound:
            if (today - quarter_end(label)).days > LATE_QUARTER_DAYS:
                log.warning("insider quarter %s ended %d days ago but is not published; "
                            "is the URL still right?", label, (today - quarter_end(label)).days)
            break  # not published yet; later quarters aren't either
        rows = _parse(data, label, ciks)
        if rows:
            _insert(conn, rows)
        loaded.add(label)
        covered &= ciks  # this quarter was read for `ciks` only; others are no longer covered
        _save_state(conn, loaded, covered)
        result.loaded.append(label)

    since = quarter_end(max(loaded)) if loaded else today - timedelta(days=120)
    result.xml_filings = sync_form4_filings(conn, sec, ciks, since)
    return result
