import io
import json
import zipfile
from datetime import date

import pytest

from analyzer.data.edgar.insider_bulk import (
    BULK_URL,
    STATE_CIKS,
    STATE_QUARTERS,
    parse_insider_zip,
    quarter_end,
    quarters_to_load,
    sync_insider,
)
from analyzer.data.http import FetchError
from analyzer.store.db import connect, get_state, migrate
from tests.helpers import FakeResponse, fake_client


def tsv(header, rows):
    return "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)


def make_zip(issuer=320193, accession="0000320193-24-000001"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(
            ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"],
            [(accession, "05-JAN-2024", "4", str(issuer)),
             ("0000000001-24-000009", "05-JAN-2024", "4", "1"),
             ("0000320193-24-000002", "06-JAN-2024", "3", str(issuer))]))
        z.writestr("REPORTINGOWNER.tsv", tsv(
            ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
            [(accession, "Doe Jane", "Director,Officer", "CEO"),
             (accession, "Roe Rick", "Director", "")]))
        z.writestr("NONDERIV_TRANS.tsv", tsv(
            ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
             "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"],
            [(accession, "03-JAN-2024", "P", "1000", "50.5", "A"),
             (accession, "03-JAN-2024", "S", "200", "", "D"),
             ("0000000001-24-000009", "03-JAN-2024", "P", "1", "1", "A")]))
    return buf.getvalue()


def test_parse_insider_zip_filters_and_normalizes():
    rows = parse_insider_zip(make_zip(), {320193})
    assert len(rows) == 2
    first = rows[0]
    assert first[:4] == ("0000320193-24-000001", 0, 320193, "2024-01-05")
    assert first[4:7] == ("Doe Jane; Roe Rick", "Director,Officer", "CEO")
    assert first[7:13] == ("2024-01-03", "P", 1000.0, 50.5, "A", "bulk")
    assert rows[1][1] == 1 and rows[1][10] is None


def test_quarters_to_load():
    assert quarters_to_load(date(2013, 10, 10)) == ["2013q1", "2013q2", "2013q3"]
    assert quarters_to_load(date(2014, 2, 1))[-1] == "2013q4"
    assert quarter_end("2024q1") == date(2024, 3, 31)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_sync_loads_published_quarters_and_replaces_xml_rows(conn, tmp_path):
    conn.execute("INSERT INTO insider_transactions VALUES "
                 "('0000320193-24-000001', 0, 320193, '2024-01-05', NULL, NULL, NULL, "
                 "'2024-01-03', 'P', 1, 1, 'A', 'xml')")

    def handler(url, params):
        if url == BULK_URL.format(label="2013q1"):
            return FakeResponse(content=make_zip())
        return FakeResponse(404)

    client, _ = fake_client(tmp_path, handler=handler)
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.loaded == ["2013q1"]
    sources = conn.execute("SELECT DISTINCT source FROM insider_transactions").fetchall()
    assert sources == [("bulk",)]


def test_new_cik_is_backfilled_from_cached_zips(conn, tmp_path):
    def handler(url, params):
        if url == BULK_URL.format(label="2013q1"):
            return FakeResponse(content=make_zip(issuer=320193))
        return FakeResponse(404)

    client, session = fake_client(tmp_path, handler=handler)
    sync_insider(conn, client, {1}, date(2013, 10, 10))
    calls_before = len(session.calls)
    result = sync_insider(conn, client, {1, 320193}, date(2013, 10, 10))
    assert result.backfilled_ciks == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE issuer_cik = 320193").fetchone()[0] == 2
    zip_calls = [c for c in session.calls[calls_before:] if c["url"].endswith("2013q1_form345.zip")]
    assert zip_calls == []


SUB_H = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"]
OWN_H = ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]
TRANS_H = ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
           "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"]


def zip_of(sub, owners=(), trans=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(SUB_H, sub))
        z.writestr("REPORTINGOWNER.tsv", tsv(OWN_H, owners))
        z.writestr("NONDERIV_TRANS.tsv", tsv(TRANS_H, trans))
    return buf.getvalue()


def serve(zips):
    """Handler serving {label: bytes | FakeResponse} (read live); every other URL is a 404."""
    def handler(url, params):
        for label, body in zips.items():
            if url == BULK_URL.format(label=label):
                return FakeResponse(content=body) if isinstance(body, bytes) else body
        return FakeResponse(404)
    return handler


def zip_urls(session, label):
    return [c for c in session.calls if c["url"] == BULK_URL.format(label=label)]


def state(conn, key):
    return json.loads(get_state(conn, key))


def test_parse_skips_unreadable_filing_dates_and_issuers():
    data = zip_of(
        [("A-1", "not a date", "4", "320193"), ("A-2", "05-JAN-2024", "4", ""),
         ("A-3", "05-JAN-2024", "4/A", "320193")],
        trans=[("A-1", "03-JAN-2024", "P", "1", "1", "A"), ("A-2", "03-JAN-2024", "P", "1", "1", "A"),
               ("A-3", "03-JAN-2024", "P", "5", "2", "A")])
    rows = parse_insider_zip(data, {320193})
    assert [(r[0], r[3]) for r in rows] == [("A-3", "2024-01-05")]
    assert rows[0][4:7] == (None, None, None)  # no reporting-owner row


def test_parse_with_no_transactions_for_tracked_filings():
    data = zip_of([("A-1", "05-JAN-2024", "4", "320193")],
                  trans=[("A-9", "03-JAN-2024", "P", "1", "1", "A")])
    assert parse_insider_zip(data, {320193}) == []


def test_bad_bodies_are_rejected_and_never_cached(conn, tmp_path):
    zips = {"2013q1": b"<html>Request Rate Threshold Exceeded</html>"}
    client, session = fake_client(tmp_path, handler=serve(zips))
    with pytest.raises(FetchError):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))
    only_submission = io.BytesIO()
    with zipfile.ZipFile(only_submission, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(SUB_H, []))
    zips["2013q1"] = only_submission.getvalue()
    with pytest.raises(FetchError):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert not client.cache_path("sec/insider/2013q1.zip").exists()
    assert get_state(conn, STATE_QUARTERS) is None
    zips["2013q1"] = make_zip()
    assert sync_insider(conn, client, {320193}, date(2013, 10, 10)).loaded == ["2013q1"]
    assert len(zip_urls(session, "2013q1")) == 3  # nothing bad was served from cache


def test_state_records_loaded_quarters_and_covered_ciks(conn, tmp_path):
    client, _ = fake_client(tmp_path, handler=serve({"2013q1": make_zip(), "2013q2": make_zip()}))
    sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert state(conn, STATE_QUARTERS) == ["2013q1", "2013q2"]
    assert state(conn, STATE_CIKS) == [320193]


def test_rerun_neither_reloads_nor_duplicates(conn, tmp_path):
    client, session = fake_client(tmp_path, handler=serve({"2013q1": make_zip()}))
    sync_insider(conn, client, {320193}, date(2013, 10, 10))
    again = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert again.loaded == [] and again.backfilled_ciks == 0
    assert len(zip_urls(session, "2013q1")) == 1
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 2


def test_failure_midway_keeps_the_quarters_already_loaded(conn, tmp_path):
    zips = {"2013q1": make_zip(), "2013q2": FakeResponse(503)}
    client, session = fake_client(tmp_path, handler=serve(zips))
    with pytest.raises(FetchError):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert state(conn, STATE_QUARTERS) == ["2013q1"]
    assert state(conn, STATE_CIKS) == [320193]
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 2
    zips["2013q2"] = make_zip(accession="0000320193-24-000011")
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.loaded == ["2013q2"]
    assert len(zip_urls(session, "2013q1")) == 1
    assert state(conn, STATE_QUARTERS) == ["2013q1", "2013q2"]


def test_cik_left_out_of_a_quarter_is_backfilled_when_it_returns(conn, tmp_path):
    zips = {"2013q1": make_zip()}
    client, session = fake_client(tmp_path, handler=serve(zips))
    sync_insider(conn, client, {320193}, date(2013, 10, 10))
    zips["2013q2"] = make_zip(accession="0000320193-24-000011")
    # 320193 drops out of the tracked set; the quarters published meanwhile skip it
    sync_insider(conn, client, {1}, date(2014, 1, 10))
    assert state(conn, STATE_QUARTERS) == ["2013q1", "2013q2"]
    assert state(conn, STATE_CIKS) == [1]
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions "
                        "WHERE accession = '0000320193-24-000011'").fetchone()[0] == 0
    downloads = len(zip_urls(session, "2013q1")) + len(zip_urls(session, "2013q2"))
    result = sync_insider(conn, client, {1, 320193}, date(2014, 1, 10))
    assert result.backfilled_ciks == 1
    # served from the disk cache
    assert len(zip_urls(session, "2013q1")) + len(zip_urls(session, "2013q2")) == downloads
    for accession in ("0000320193-24-000001", "0000320193-24-000011"):
        assert conn.execute("SELECT COUNT(*) FROM insider_transactions WHERE accession = ?",
                            (accession,)).fetchone()[0] == 2


def test_form4_xml_covers_filings_after_the_last_loaded_quarter(conn, tmp_path):
    conn.executemany("INSERT INTO filings VALUES (?, ?, ?, ?, NULL, NULL, ?)", [
        ("0000320193-13-000050", 320193, "4", "2013-03-30", "old.xml"),  # inside loaded quarter
        ("0000320193-13-000060", 320193, "4", "2013-04-02", "new.xml"),
    ])
    form4 = (b"<ownershipDocument><issuer><issuerCik>320193</issuerCik></issuer>"
             b"<nonDerivativeTable><nonDerivativeTransaction>"
             b"<transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
             b"</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>")

    def handler(url, params):
        if url == BULK_URL.format(label="2013q1"):
            return FakeResponse(content=make_zip())
        if url.endswith("new.xml"):
            return FakeResponse(content=form4)
        return FakeResponse(404)

    client, _ = fake_client(tmp_path, handler=handler)
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.xml_filings == 1
    assert conn.execute("SELECT source FROM insider_transactions "
                        "WHERE accession = '0000320193-13-000060'").fetchall() == [("xml",)]
