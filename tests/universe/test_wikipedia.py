from datetime import date

import pytest

from analyzer.store.db import connect, migrate
from analyzer.universe.membership import members_on
from analyzer.universe.wikipedia import (
    API_URL,
    month_starts,
    normalize_ticker,
    parse_constituents,
    snapshot_for,
    sync_index_snapshots,
)
from tests.helpers import FakeResponse, fake_client


def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{body}</table>"


def page(tickers, old_headers=False):
    if old_headers:
        headers = ["Ticker symbol", "Company", "GICS Sector", "GICS Sub Industry"]
        rows = [(t, f"{t} Corp", "Information Technology", "Semiconductors") for t in tickers]
    else:
        headers = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added"]
        rows = [(t, f"{t} Corp", "Health Care", "Biotechnology", "2001-08-07") for t in tickers]
    navbox = table(["x"], [("y",)])
    return f"<html><body>{table(headers, rows)}{navbox}</body></html>"


def names(n, offset=0):
    return [f"T{i}" for i in range(offset, offset + n)]


@pytest.mark.parametrize(
    "raw, expected",
    [("brk.b", "BRK-B"), ("GOOGL[3]", "GOOGL"), (" MMM ", "MMM"), ("", None), ("N/A", None)],
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
