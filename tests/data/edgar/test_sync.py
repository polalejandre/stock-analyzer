from datetime import datetime, timedelta

from analyzer.data.edgar.companies import SUBMISSIONS_URL
from analyzer.data.edgar.companyfacts import COMPANYFACTS_URL
from analyzer.data.edgar.sync import reextract_facts_if_tags_changed, refresh_company
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


def _store_stale_row(conn, cik, source):
    conn.execute("INSERT INTO facts VALUES (?, 'revenue', 'us-gaap:SalesRevenueGoodsNet', 'USD', "
                 "'2015-01-01', '2015-03-31', 1, '2015-05-01', '10-Q', 'old', ?)", (cik, source))


def test_refresh_company_replaces_rows_a_new_extraction_no_longer_makes(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBS if url == SUBMISSIONS_URL.format(cik=320193) else CF)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    _store_stale_row(conn, 320193, "companyfacts")
    _store_stale_row(conn, 999, "companyfacts")  # another company is not touched
    refresh_company(conn, client, 320193, max_age=timedelta(0))
    assert conn.execute("SELECT cik, accession FROM facts ORDER BY cik").fetchall() == [
        (999, "old"), (320193, "a1")]


def test_refresh_company_keeps_instance_rows(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBS if url == SUBMISSIONS_URL.format(cik=320193) else CF)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    _store_stale_row(conn, 320193, "instance")
    refresh_company(conn, client, 320193, max_age=timedelta(0))
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE source = 'instance'").fetchone() == (1,)


def test_reextract_reuses_cached_companyfacts_once_per_tag_map(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBS if url == SUBMISSIONS_URL.format(cik=320193) else CF)

    client, session = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=timedelta(0))  # caches companyfacts
    conn.execute("DELETE FROM facts")
    calls = len(session.calls)
    assert reextract_facts_if_tags_changed(conn, client, [320193]) == 1
    assert conn.execute("SELECT accession FROM facts").fetchall() == [("a1",)]
    assert len(session.calls) == calls  # served from the cache
    assert reextract_facts_if_tags_changed(conn, client, [320193]) == 0  # same tag map: nothing


def test_reextract_is_repeated_next_run_when_a_fetch_fails(tmp_path):
    client, _ = fake_client(tmp_path, responses=[FakeResponse(500)] * 10)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    assert reextract_facts_if_tags_changed(conn, client, [320193]) == 0
    assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone() == (0,)


def test_reextract_survives_a_company_whose_facts_cannot_be_read(tmp_path, monkeypatch):
    from analyzer.data.edgar import sync

    client, _ = fake_client(tmp_path, handler=lambda url, params: FakeResponse(json_data=CF))
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    real = sync.extract_facts

    def flaky(cf, cik):
        if cik == 1:
            raise KeyError("end")
        return real(cf, cik)

    monkeypatch.setattr(sync, "extract_facts", flaky)
    assert reextract_facts_if_tags_changed(conn, client, [1, 320193]) == 1
    assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone() == (0,)  # retried next run
