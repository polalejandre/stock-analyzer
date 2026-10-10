import io
import json
import logging
import sqlite3
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


def filings_zip(n, *, bad_dates=(), bad_ciks=(), date_text="05-JAN-2024"):
    """n Form 4 filings (A-000...) for issuer 320193, one transaction each."""
    sub = [(f"A-{i:03d}", "garbage" if i in bad_dates else date_text, "4",
            "" if i in bad_ciks else "320193") for i in range(n)]
    trans = [(f"A-{i:03d}", "03-JAN-2024", "P", "1", "1", "A") for i in range(n)]
    return zip_of(sub, trans=trans)


def test_parse_filing_without_owner_rows_has_none_owner_fields():
    data = zip_of([("A-3", "05-JAN-2024", "4/A", "320193")],
                  trans=[("A-3", "03-JAN-2024", "P", "5", "2", "A")])
    rows = parse_insider_zip(data, {320193})
    assert [(r[0], r[3]) for r in rows] == [("A-3", "2024-01-05")]
    assert rows[0][4:7] == (None, None, None)


def test_parse_skips_a_tiny_minority_of_unreadable_values(caplog):
    with caplog.at_level(logging.WARNING):
        rows = parse_insider_zip(filings_zip(100, bad_dates={7}, bad_ciks={50}), {320193})
    assert len(rows) == 98
    assert {"A-007", "A-050"}.isdisjoint(r[0] for r in rows)
    assert "FILING_DATE" in caplog.text and "ISSUERCIK" in caplog.text


@pytest.mark.parametrize("bad, raises", [(5, False), (6, True)])
def test_parse_threshold_is_five_percent_of_tracked_filings(bad, raises):
    data = filings_zip(100, bad_dates=set(range(bad)))
    if raises:
        with pytest.raises(ValueError, match="6 of 100.*FILING_DATE.*'garbage'"):
            parse_insider_zip(data, {320193})
    else:
        assert len(parse_insider_zip(data, {320193})) == 95


def test_parse_raises_when_every_date_or_issuer_is_unreadable():
    with pytest.raises(ValueError, match="FILING_DATE.*'2024/01/05'"):
        parse_insider_zip(filings_zip(100, date_text="2024/01/05"), {320193})
    with pytest.raises(ValueError, match="100 of 100.*ISSUERCIK"):
        parse_insider_zip(filings_zip(100, bad_ciks=set(range(100))), {320193})


def test_parse_ignores_untracked_filings_when_counting_bad_dates():
    sub = [("A-1", "05-JAN-2024", "4", "320193")] + [(f"B-{i}", "garbage", "4", "999")
                                                    for i in range(50)]
    data = zip_of(sub, trans=[("A-1", "03-JAN-2024", "P", "1", "1", "A")])
    assert len(parse_insider_zip(data, {320193})) == 1


def test_parse_duplicate_submission_rows_do_not_double_transactions():
    data = zip_of([("A-1", "05-JAN-2024", "4", "320193")] * 2,
                  trans=[("A-1", "03-JAN-2024", "P", "1", "1", "A")])
    rows = parse_insider_zip(data, {320193})
    assert [(r[0], r[1]) for r in rows] == [("A-1", 0)]


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

    client, session = fake_client(tmp_path, handler=handler)
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.xml_filings == 1
    assert conn.execute("SELECT source FROM insider_transactions "
                        "WHERE accession = '0000320193-13-000060'").fetchall() == [("xml",)]
    assert [c for c in session.calls if c["url"].endswith("old.xml")] == []
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions "
                        "WHERE accession = '0000320193-13-000050'").fetchone()[0] == 0


def test_quarter_with_unreadable_dates_is_not_marked_loaded(conn, tmp_path):
    zips = {"2013q1": filings_zip(100, date_text="2024/01/05")}
    client, _ = fake_client(tmp_path, handler=serve(zips))
    with pytest.raises(ValueError, match="2013q1.*FILING_DATE"):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert get_state(conn, STATE_QUARTERS) is None
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 0


def test_one_unreadable_date_in_a_quarter_is_skipped_and_the_quarter_loads(conn, tmp_path):
    client, _ = fake_client(tmp_path, handler=serve({"2013q1": filings_zip(100, bad_dates={3})}))
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.loaded == ["2013q1"]
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 99


def test_state_keys_are_written_in_one_transaction(conn, tmp_path):
    zips = {"2013q1": make_zip()}
    client, _ = fake_client(tmp_path, handler=serve(zips))
    sync_insider(conn, client, {320193}, date(2013, 10, 10))
    # the second key's write fails, as if the process died between the two upserts
    conn.execute(
        f"CREATE TRIGGER fail_ciks BEFORE INSERT ON sync_state WHEN NEW.key = '{STATE_CIKS}' "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    zips["2013q2"] = make_zip(accession="0000320193-24-000011")
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert state(conn, STATE_QUARTERS) == ["2013q1"]
    assert state(conn, STATE_CIKS) == [320193]
    # the rows landed, so the next run re-reads the quarter harmlessly
    conn.execute("DROP TRIGGER fail_ciks")
    assert sync_insider(conn, client, {320193}, date(2013, 10, 10)).loaded == ["2013q2"]
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 4
    assert state(conn, STATE_QUARTERS) == ["2013q1", "2013q2"]


def test_missing_old_quarter_warns_but_a_recent_gap_does_not(conn, tmp_path, caplog):
    client, _ = fake_client(tmp_path, handler=serve({}))
    with caplog.at_level(logging.WARNING):
        sync_insider(conn, client, {320193}, date(2013, 10, 10))  # 2013q1 ended 193 days ago
    assert "2013q1" in caplog.text and "not published" in caplog.text
    caplog.clear()
    client, _ = fake_client(tmp_path, handler=serve({"2013q1": make_zip()}))
    with caplog.at_level(logging.WARNING):
        sync_insider(conn, client, {320193}, date(2013, 7, 20))  # q2 ended 20 days ago
    assert caplog.text == ""


def test_backfill_skips_an_evicted_quarter_and_retries_the_ciks_later(conn, tmp_path, caplog):
    zips = {"2013q1": make_zip()}
    client, session = fake_client(tmp_path, handler=serve(zips))
    sync_insider(conn, client, {1}, date(2013, 10, 10))
    client.cache_path("sec/insider/2013q1.zip").unlink()
    gone = zips.pop("2013q1")  # evicted from disk and no longer served
    with caplog.at_level(logging.WARNING):
        result = sync_insider(conn, client, {1, 320193}, date(2013, 10, 10))
    assert "2013q1" in caplog.text
    assert result.backfilled_ciks == 0
    assert state(conn, STATE_CIKS) == [1]  # 320193 is not covered, so it retries
    assert conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE issuer_cik = 320193").fetchone()[0] == 0
    zips["2013q1"] = gone
    result = sync_insider(conn, client, {1, 320193}, date(2013, 10, 10))
    assert result.backfilled_ciks == 1
    assert state(conn, STATE_CIKS) == [1, 320193]
    assert conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE issuer_cik = 320193").fetchone()[0] == 2
