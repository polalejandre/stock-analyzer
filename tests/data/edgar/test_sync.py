from datetime import datetime, timedelta

from analyzer.data.edgar.companies import SUBMISSIONS_URL
from analyzer.data.edgar.companyfacts import COMPANYFACTS_URL
from analyzer.data.edgar.sync import refresh_company
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

SUBS = {"name": "Apple Inc.", "sic": "3571", "sicDescription": "Computers",
        "filings": {"recent": {"accessionNumber": ["a1"], "form": ["10-Q"],
                               "filingDate": ["2026-08-01"], "reportDate": ["2026-06-27"],
                               "items": [""], "primaryDocument": ["x.htm"]}, "files": []}}
CF = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"start": "2026-03-29", "end": "2026-06-27", "val": 9e10, "filed": "2026-08-01",
     "form": "10-Q", "accn": "a1"}]}}}}}


def test_refresh_company_stores_submissions_and_facts(tmp_path):
    def handler(url, params):
        if url == SUBMISSIONS_URL.format(cik=320193):
            return FakeResponse(json_data=SUBS)
        assert url == COMPANYFACTS_URL.format(cik=320193)
        return FakeResponse(json_data=CF)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=timedelta(0), now=datetime(2026, 10, 10))
    assert conn.execute("SELECT sic FROM companies").fetchone() == ("3571",)
    assert conn.execute("SELECT value FROM facts").fetchone() == (9e10,)


def test_refresh_company_without_facts(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(json_data=SUBS)])
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=None, facts=False)
    assert len(session.calls) == 1


def test_max_age_none_reuses_the_cache_and_zero_always_refetches(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBS if url == SUBMISSIONS_URL.format(cik=320193) else CF)

    client, session = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=timedelta(0))  # fills the cache: 2 requests
    assert len(session.calls) == 2
    refresh_company(conn, client, 320193, max_age=None)
    assert len(session.calls) == 2  # any cached copy is reused
    refresh_company(conn, client, 320193, max_age=timedelta(0))
    assert len(session.calls) == 4  # always refetched
