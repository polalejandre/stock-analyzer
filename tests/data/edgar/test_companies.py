from datetime import date, datetime

from analyzer.data.edgar.companies import (
    SUBMISSIONS_URL,
    fetch_submissions,
    load_ticker_map,
    store_submissions,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

TICKERS = {"fields": ["cik", "name", "ticker", "exchange"],
           "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"],
                    [1067983, "Berkshire Hathaway", "brk.b", "NYSE"],
                    [1, "Dup", "AAPL", "OTC"]]}


def filings_block(rows):
    keys = ["accessionNumber", "form", "filingDate", "reportDate", "items", "primaryDocument"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


SUBMISSIONS = {
    "cik": "320193", "name": "Apple Inc.", "sic": "3571", "sicDescription": "Electronic Computers",
    "filings": {
        "recent": filings_block([
            ("0000320193-26-000100", "10-Q", "2026-08-01", "2026-06-27", "", "aapl-20260627.htm"),
            ("0000320193-26-000090", "8-K", "2026-07-31", "2026-07-31", "2.02,9.01", "a8k.htm"),
            ("0000320193-26-000080", "S-8", "2026-07-01", "", "", "s8.htm"),
        ]),
        "files": [
            {"name": "CIK0000320193-submissions-001.json", "filingFrom": "2012-01-01", "filingTo": "2016-12-31"},
            {"name": "CIK0000320193-submissions-002.json", "filingFrom": "1994-01-01", "filingTo": "2011-12-31"},
        ],
    },
}
OLD_PAGE = filings_block([
    ("0000320193-15-000001", "10-K", "2015-10-28", "2015-09-26", "", "aapl10k.htm"),
    ("0000320193-12-000001", "10-K", "2012-10-31", "2012-09-29", "", "old.htm"),
])


def test_load_ticker_map_normalizes_and_dedupes(tmp_path):
    client, _ = fake_client(tmp_path, responses=[FakeResponse(json_data=TICKERS)])
    df = load_ticker_map(client)
    assert df.ticker.tolist() == ["AAPL", "BRK-B"]
    assert df.set_index("ticker").at["AAPL", "cik"] == 320193


def test_fetch_submissions_reads_recent_and_needed_older_pages(tmp_path):
    def handler(url, params):
        if url == SUBMISSIONS_URL.format(cik=320193):
            return FakeResponse(json_data=SUBMISSIONS)
        assert url.endswith("CIK0000320193-submissions-001.json")  # -002 ends before `since`
        return FakeResponse(json_data=OLD_PAGE)

    client, _ = fake_client(tmp_path, handler=handler)
    subs = fetch_submissions(client, 320193, since=date(2013, 1, 1))
    assert subs.sic == "3571"
    assert subs.filings.accession.tolist() == [
        "0000320193-26-000100", "0000320193-26-000090", "0000320193-15-000001"]
    assert subs.filings.set_index("form").at["8-K", "items"] == "2.02,9.01"


def test_store_submissions(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBMISSIONS if "CIK0000320193.json" in url else OLD_PAGE)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    store_submissions(conn, fetch_submissions(client, 320193), datetime(2026, 10, 10))
    assert conn.execute("SELECT name, sic FROM companies").fetchall() == [("Apple Inc.", "3571")]
    assert conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0] == 3
