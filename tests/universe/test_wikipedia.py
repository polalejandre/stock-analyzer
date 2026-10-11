import json
from datetime import date, timedelta

import pytest

from analyzer.data.http import FetchError
from analyzer.store.db import connect, get_state, migrate, set_state
from analyzer.universe.membership import members_on
from analyzer.universe.wikipedia import (
    API_URL,
    month_starts,
    normalize_ticker,
    parse_constituents,
    revisions_before,
    snapshot_for,
    sync_index_snapshots,
)
from tests.helpers import FakeResponse, fake_client


def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{body}</table>"


def page(tickers, old_headers=False, added=None):
    if old_headers:
        headers = ["Ticker symbol", "Company", "GICS Sector", "GICS Sub Industry"]
        rows = [(t, f"{t} Corp", "Information Technology", "Semiconductors") for t in tickers]
    else:
        headers = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added"]
        rows = [(t, f"{t} Corp", "Health Care", "Biotechnology", (added or {}).get(t, "2001-08-07")) for t in tickers]
    navbox = table(["x"], [("y",)])
    return f"<html><body>{table(headers, rows)}{navbox}</body></html>"


def names(n, offset=0):
    return [f"T{i}" for i in range(offset, offset + n)]


@pytest.mark.parametrize(
    "raw, expected",
    [("brk.b", "BRK-B"), ("GOOGL[3]", "GOOGL"), (" MMM ", "MMM"), ("", None), ("N/A", None), (float("nan"), None), (None, None)],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


def test_parse_current_layout():
    df = parse_constituents(page(names(60) + ["BRK.B"]))
    assert len(df) == 61
    row = df[df.ticker == "BRK-B"].iloc[0]
    assert row.gics_sector == "Health Care"
    assert row.gics_sub_industry == "Biotechnology"
    assert row.date_added == "2001-08-07"


def test_parse_old_layout():
    df = parse_constituents(page(names(60), old_headers=True))
    assert len(df) == 60
    assert df.iloc[0].gics_sub_industry == "Semiconductors"
    assert df.iloc[0].date_added is None


def test_parse_without_table_raises():
    with pytest.raises(ValueError):
        parse_constituents("<html><body><p>vandalized</p></body></html>")


def test_month_starts():
    assert month_starts(date(2026, 7, 15), date(2026, 10, 10)) == [
        date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)
    ]


def revisions_json(*revids):
    return {"query": {"pages": [{"revisions": [
        {"revid": r, "timestamp": "2026-01-01T00:00:00Z"} for r in revids]}]}}


def test_snapshot_skips_vandalized_revision(tmp_path):
    def handler(url, params):
        if url == API_URL:
            return FakeResponse(json_data=revisions_json(2, 1))
        html = page(names(10)) if params["oldid"] == 2 else page(names(490))
        return FakeResponse(content=html)

    client, _ = fake_client(tmp_path, handler=handler)
    rev, df = snapshot_for(client, "sp500", date(2026, 9, 1), today=date(2026, 10, 10))
    assert rev.revid == 1
    assert len(df) == 490


def test_sync_builds_membership_from_snapshots(tmp_path):
    def handler(url, params):
        if url == API_URL:
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        tickers = names(490) if params["oldid"] < 20260901 else names(490, offset=1)
        return FakeResponse(content=page(tickers))

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    result = sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=date(2026, 8, 1))
    assert result.failed == []
    assert result.attempted == 4  # 08-01, 09-01, 10-01 and today
    assert "T0" in members_on(conn, "sp500", date(2026, 8, 15))
    assert "T0" not in members_on(conn, "sp500", date(2026, 9, 1))
    assert "T490" in members_on(conn, "sp500", date(2026, 9, 1))
    assert len(members_on(conn, "sp500", date(2026, 10, 5))) == 490

    # A later day's run replaces the previous "today" snapshot instead of adding one.
    sync_index_snapshots(conn, client, "sp500", date(2026, 10, 11), start=date(2026, 8, 1))
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM index_snapshots ORDER BY 1")]
    assert dates == ["2026-08-01", "2026-09-01", "2026-10-01", "2026-10-11"]


def test_snapshot_rejects_undersized_revision(tmp_path):
    def handler(url, params):
        if url == API_URL:
            return FakeResponse(json_data=revisions_json(2, 1))
        return FakeResponse(content=page(names(100) if params["oldid"] == 2 else names(490)))

    client, _ = fake_client(tmp_path, handler=handler)
    rev, df = snapshot_for(client, "sp500", date(2026, 9, 1), today=date(2026, 10, 10))
    assert (rev.revid, len(df)) == (1, 490)


def test_snapshot_falls_back_when_revision_fetch_fails(tmp_path):
    def handler(url, params):
        if url == API_URL:
            return FakeResponse(json_data=revisions_json(2, 1))
        if params["oldid"] == 2:
            return FakeResponse(status_code=404)
        return FakeResponse(content=page(names(490)))

    client, _ = fake_client(tmp_path, handler=handler)
    rev, _ = snapshot_for(client, "sp500", date(2026, 9, 1), today=date(2026, 10, 10))
    assert rev.revid == 1


def test_api_error_body_is_not_cached(tmp_path):
    client, session = fake_client(
        tmp_path,
        responses=[FakeResponse(json_data={"error": {"code": "ratelimited"}}),
                   FakeResponse(json_data=revisions_json(7))],
    )
    args = (client, "List_of_S&P_500_companies", date(2026, 1, 1), date(2026, 10, 10))
    with pytest.raises(FetchError):
        revisions_before(*args)
    assert [r.revid for r in revisions_before(*args)] == [7]
    assert len(session.calls) == 2


def _membership_rows(conn, ticker):
    return conn.execute(
        "SELECT start_date, end_date FROM index_membership WHERE ticker = ? ORDER BY 1",
        (ticker,)).fetchall()


def test_end_date_does_not_drift_with_run_date(tmp_path):
    def handler(url, params):
        if url == API_URL:
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        # T0 leaves the page for revisions made from 2026-10-05 on.
        return FakeResponse(content=page(names(490) if params["oldid"] < 20261006
                                         else names(490, offset=1)))

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for day in (date(2026, 10, 5), date(2026, 10, 10), date(2026, 10, 20), date(2026, 11, 1)):
        sync_index_snapshots(conn, client, "sp500", day, start=date(2026, 10, 1))
        assert _membership_rows(conn, "T0") == [("2026-10-01", "2026-10-05")]
        assert "T0" not in members_on(conn, "sp500", date(2026, 10, 25))
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM index_snapshots ORDER BY 1")]
    assert dates == ["2026-10-01", "2026-10-05", "2026-11-01"]


def test_failed_today_fetch_keeps_last_good_snapshot(tmp_path):
    def handler(url, params):
        if url == API_URL:
            if params["rvstart"].startswith("2026-10-12"):
                return FakeResponse(status_code=404)
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        if params["oldid"] < 20261011:
            return FakeResponse(content=page(names(490)))
        return FakeResponse(content=page(names(489, offset=1) + ["T495"]))

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    start = date(2026, 10, 1)
    assert sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=start).failed == []
    result = sync_index_snapshots(conn, client, "sp500", date(2026, 10, 11), start=start)
    assert len(result.failed) == 1
    members = members_on(conn, "sp500", date(2026, 10, 11))
    assert "T0" not in members and "T495" in members


def test_date_added_refines_membership_start(tmp_path):
    def handler(url, params):
        if url == API_URL:
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        if params["oldid"] < 20260901:
            return FakeResponse(content=page(names(490)))
        return FakeResponse(content=page(names(490) + ["NEW"], added={"NEW": "2026-08-20"}))

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=date(2026, 8, 1))
    assert _membership_rows(conn, "NEW") == [("2026-08-20", None)]


# --- final review I3: an unfetchable historical month is given up after 3 runs ---------------

def test_revisions_response_with_no_pages_is_rejected_and_not_cached(tmp_path):
    client, session = fake_client(
        tmp_path,
        responses=[FakeResponse(json_data={"query": {"pages": []}}),
                   FakeResponse(json_data=revisions_json(7))],
    )
    args = (client, "List_of_S&P_500_companies", date(2026, 1, 1), date(2026, 10, 10))
    with pytest.raises(FetchError):
        revisions_before(*args)
    assert [r.revid for r in revisions_before(*args)] == [7]
    assert len(session.calls) == 2


def _broken_month_client(tmp_path, broken: str):
    """Every revision is fine except those asked for the `broken` rvstart day (a 404)."""
    def handler(url, params):
        if url == API_URL:
            if params["rvstart"].startswith(broken):
                return FakeResponse(status_code=404)
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        return FakeResponse(content=page(names(490)))

    return fake_client(tmp_path, handler=handler)


def _asked(session, day: str) -> int:
    return sum(1 for c in session.calls if c["params"].get("rvstart", "").startswith(day))


def test_historical_month_failing_three_runs_is_skipped_on_the_fourth(tmp_path, caplog):
    client, session = _broken_month_client(tmp_path, "2026-09-01")
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    start = date(2026, 8, 1)
    for run, day in enumerate((date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)), 1):
        caplog.clear()
        result = sync_index_snapshots(conn, client, "sp500", day, start=start)
        assert [item for item, _ in result.failed] == ["sp500 2026-09-01"]
        assert _asked(session, "2026-09-01") == run
        assert ("giving up" in caplog.text) == (run == 3)
    caplog.clear()
    result = sync_index_snapshots(conn, client, "sp500", date(2026, 10, 13), start=start)
    assert _asked(session, "2026-09-01") == 3          # not requested again
    assert result.failed == []
    assert result.attempted == 1                        # only today's snapshot
    assert result.skipped == ["sp500 2026-09-01"]
    assert "giving up" not in caplog.text               # warned once, on the third failure
    assert json.loads(get_state(conn, "wiki_month_failures")) == {"sp500 2026-09-01": 3}


def test_an_outage_never_gives_up_on_unfetched_months(tmp_path):
    """Wikipedia down for days: today's snapshot fails too, so no month failure is counted."""
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    start = date(2026, 8, 1)
    for n in range(5):
        down, _ = _broken_month_client(tmp_path / str(n), "")  # every request fails
        result = sync_index_snapshots(conn, down, "sp500", date(2026, 10, 10 + n), start=start)
        assert result.skipped == []
        assert len(result.failed) == 4                  # three month starts and today
    assert json.loads(get_state(conn, "wiki_month_failures") or "{}") == {}
    up, _ = _broken_month_client(tmp_path / "up", "1999-01-01")
    result = sync_index_snapshots(conn, up, "sp500", date(2026, 10, 15), start=start)
    assert result.failed == [] and result.attempted == 4   # the months are still fetched


def test_same_day_reruns_count_a_month_failure_once(tmp_path):
    client, session = _broken_month_client(tmp_path, "2026-09-01")
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for _ in range(4):
        result = sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=date(2026, 8, 1))
        assert result.skipped == []
    assert _asked(session, "2026-09-01") == 4
    assert json.loads(get_state(conn, "wiki_month_failures")) == {"sp500 2026-09-01": 1}


def test_a_month_that_recovers_clears_its_failure_count(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    start = date(2026, 8, 1)
    broken, _ = _broken_month_client(tmp_path / "a", "2026-09-01")
    for day in (date(2026, 10, 10), date(2026, 10, 11)):
        sync_index_snapshots(conn, broken, "sp500", day, start=start)
    assert json.loads(get_state(conn, "wiki_month_failures")) == {"sp500 2026-09-01": 2}
    fixed, _ = _broken_month_client(tmp_path / "b", "1999-01-01")
    assert sync_index_snapshots(conn, fixed, "sp500", date(2026, 10, 12), start=start).failed == []
    assert json.loads(get_state(conn, "wiki_month_failures")) == {}


def test_todays_snapshot_failure_always_counts(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for n in range(4):
        today = date(2026, 10, 10 + n)
        client, session = _broken_month_client(tmp_path / str(n), (today + timedelta(days=1)).isoformat())
        result = sync_index_snapshots(conn, client, "sp500", today, start=date(2026, 10, 1))
        assert [item for item, _ in result.failed] == [f"sp500 {today.isoformat()}"]
        assert result.skipped == []
    assert json.loads(get_state(conn, "wiki_month_failures") or "{}") == {}


def test_a_failing_first_of_the_month_today_is_never_skipped(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    today = date(2026, 11, 1)
    for n in range(4):
        client, _ = _broken_month_client(tmp_path / str(n), "2026-11-02")
        result = sync_index_snapshots(conn, client, "sp500", today, start=date(2026, 11, 1))
        assert [item for item, _ in result.failed] == ["sp500 2026-11-01"]


def test_unreadable_failure_state_only_costs_retries(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for bad in ("nope", "[1]", '{"sp500 2026-09-01": "x"}'):
        set_state(conn, "wiki_month_failures", bad)
        client, session = _broken_month_client(tmp_path / str(len(bad)), "2026-09-01")
        sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=date(2026, 8, 1))
        assert _asked(session, "2026-09-01") == 1
