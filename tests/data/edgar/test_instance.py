from datetime import date

from analyzer.data.edgar.instance import (
    FILE_URL,
    FILING_INDEX_URL,
    apply_instance_fallback,
    find_instance_name,
    parse_instance,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

XML = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:dei="http://xbrl.sec.gov/dei/2025"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <xbrli:context id="Q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="SEG"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">x:A</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <xbrli:unit id="usdPerShare"><xbrli:divide>
    <xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
    <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
  </xbrli:divide></xbrli:unit>
  <us-gaap:Revenues contextRef="Q" unitRef="usd" decimals="-6">5000000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="SEG" unitRef="usd" decimals="-6">1000</us-gaap:Revenues>
  <us-gaap:EarningsPerShareDiluted contextRef="Q" unitRef="usdPerShare" decimals="2">1.25</us-gaap:EarningsPerShareDiluted>
  <us-gaap:Assets contextRef="I" unitRef="usd">90000</us-gaap:Assets>
  <us-gaap:Assets contextRef="Q" unitRef="usd">1</us-gaap:Assets>
  <dei:EntityCommonStockSharesOutstanding contextRef="I" unitRef="shares">1000000</dei:EntityCommonStockSharesOutstanding>
  <us-gaap:GrossProfit contextRef="Q" unitRef="usd" xsi:nil="true"/>
  <us-gaap:SomethingElse contextRef="Q" unitRef="usd">7</us-gaap:SomethingElse>
</xbrli:xbrl>"""

INDEX = {"directory": {"item": [{"name": "FilingSummary.xml"}, {"name": "aapl-20260630_htm.xml"},
                                {"name": "aapl-20260630_cal.xml"}, {"name": "aapl-20260630.htm"}]}}


def test_find_instance_name():
    assert find_instance_name(INDEX) == "aapl-20260630_htm.xml"
    legacy = {"directory": {"item": [{"name": "x_lab.xml"}, {"name": "aapl-20120630.xml"}]}}
    assert find_instance_name(legacy) == "aapl-20120630.xml"
    assert find_instance_name({"directory": {"item": [{"name": "a.htm"}]}}) is None


def test_parse_instance_keeps_plain_facts_only():
    rows = parse_instance(XML, cik=1, accession="0000000001-26-000001", form="10-Q",
                          filed="2026-08-01")
    got = {(r[1], r[4], r[5], r[6]) for r in rows}
    assert got == {
        ("revenue", "2026-04-01", "2026-06-30", 5e9),
        ("eps_diluted", "2026-04-01", "2026-06-30", 1.25),
        ("total_assets", "", "2026-06-30", 90000.0),
        ("shares_outstanding", "", "2026-06-30", 1e6),
    }
    assert all(r[10] == "instance" for r in rows)


def test_apply_fallback_fills_missing_latest_filing(tmp_path):
    acc, cik = "0000000001-26-000001", 1
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.execute("INSERT INTO filings VALUES (?, ?, '10-Q', '2026-08-01', '2026-06-30', NULL, 'a.htm')",
                 (acc, cik))

    def handler(url, params):
        if url == FILING_INDEX_URL.format(cik=cik, acc=acc.replace("-", "")):
            return FakeResponse(json_data=INDEX)
        assert url == FILE_URL.format(cik=cik, acc=acc.replace("-", ""), name="aapl-20260630_htm.xml")
        return FakeResponse(content=XML)

    client, session = fake_client(tmp_path, handler=handler)
    assert apply_instance_fallback(conn, client, cik, date(2026, 8, 5)) == [acc]
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE source = 'instance'").fetchone()[0] == 4
    assert apply_instance_fallback(conn, client, cik, date(2026, 8, 5)) == []
    assert len(session.calls) == 2


def test_apply_fallback_isolates_bad_filing(tmp_path):
    good, bad, cik = "0000000001-26-000002", "0000000001-26-000003", 1
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for acc, d in ((good, "2026-08-01"), (bad, "2026-08-02")):
        conn.execute("INSERT INTO filings VALUES (?, ?, '10-Q', ?, '2026-06-30', NULL, 'a.htm')",
                     (acc, cik, d))

    def handler(url, params):
        if url.endswith("index.json"):
            return FakeResponse(json_data=INDEX)
        if bad.replace("-", "") in url:
            return FakeResponse(content=b"<not xml")
        return FakeResponse(content=XML)

    client, _ = fake_client(tmp_path, handler=handler)
    assert apply_instance_fallback(conn, client, cik, date(2026, 8, 5)) == [good]
    assert not list(tmp_path.rglob(f"*{bad.replace('-', '')}*_htm.xml*"))  # bad body not cached
