from datetime import date

from analyzer.data.edgar.form4 import form4_document_url, parse_form4, sync_form4_filings
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>NKE</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer>
      <officerTitle>President and CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Roe Rick</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>61.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-16-04:00</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_document_url_strips_xsl_rendering_prefix():
    url = form4_document_url(320193, "0000320193-26-000001", "xslF345X05/wk-form4_1.xml")
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/wk-form4_1.xml"


def test_parse_form4():
    rows = parse_form4(FORM4, "0000320193-26-000001", "2026-09-17")
    assert len(rows) == 2
    acc, seq, issuer, filed, owners, rel, title, tdate, code, shares, price, ad, src = rows[0]
    assert (acc, seq, filed) == ("0000320193-26-000001", 0, "2026-09-17")
    assert rows[1][1] == 1
    assert (issuer, owners, rel, title) == (320193, "Doe Jane; Roe Rick", "Director,Officer",
                                             "President and CEO")
    assert (tdate, code, shares, price, ad, src) == ("2026-09-15", "P", 10000.0, 61.5, "A", "xml")
    assert rows[1][7] == "2026-09-16" and rows[1][10] is None


def test_sync_form4_filings_parses_new_filings_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany("INSERT INTO filings VALUES (?, ?, ?, ?, NULL, NULL, ?)", [
        ("0000320193-26-000001", 320193, "4", "2026-09-17", "xslF345X05/f.xml"),
        ("0000320193-26-000000", 320193, "4", "2026-01-02", "xslF345X05/old.xml"),  # before since
        ("0000999999-26-000001", 999999, "4", "2026-09-17", "f.xml"),               # untracked CIK
    ])
    client, session = fake_client(tmp_path, responses=[FakeResponse(content=FORM4)])
    assert sync_form4_filings(conn, client, {320193}, since=date(2026, 6, 30)) == 1
    assert sync_form4_filings(conn, client, {320193}, since=date(2026, 6, 30)) == 0
    assert len(session.calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 2


def test_bad_document_does_not_block_other_filings(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany("INSERT INTO filings VALUES (?, ?, ?, ?, NULL, NULL, ?)", [
        ("0000320193-26-000001", 320193, "4", "2026-09-17", "bad.xml"),
        ("0000320193-26-000002", 320193, "4", "2026-09-18", "good.xml"),
    ])

    def handler(url, params):
        return FakeResponse(content=b"<html>oops" if "bad.xml" in url else FORM4)

    client, _ = fake_client(tmp_path, handler=handler)
    assert sync_form4_filings(conn, client, {320193}, since=date(2026, 6, 30)) == 1
    accs = {r[0] for r in conn.execute("SELECT accession FROM insider_transactions")}
    assert accs == {"0000320193-26-000002"}
