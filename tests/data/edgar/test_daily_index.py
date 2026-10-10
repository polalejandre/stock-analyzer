from datetime import date

import pytest

from analyzer.data.edgar.daily_index import (
    POINTER_KEY,
    next_pointer,
    parse_master_index,
    scan_daily_indexes,
)
from analyzer.store.db import connect, get_state, migrate, set_state
from tests.helpers import FakeResponse, fake_client

MASTER = """Description:           Daily Index of EDGAR Dissemination Feed by Company Name
Last Data Received:    October 6, 2026

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
320193|Apple Inc.|10-Q|20261006|edgar/data/320193/0000320193-26-000100.txt
1065280|NETFLIX INC|4|20261006|edgar/data/1065280/0001065280-26-000200.txt
999|OTHER CO|8-K|20261006|edgar/data/999/0000000999-26-000001.txt
"""


def test_parse_master_index():
    entries = parse_master_index(MASTER)
    assert [(e.cik, e.form, e.accession) for e in entries] == [
        (320193, "10-Q", "0000320193-26-000100"),
        (1065280, "4", "0001065280-26-000200"),
        (999, "8-K", "0000000999-26-000001"),
    ]
    assert entries[0].date_filed == date(2026, 10, 6)


def test_pointer_waits_for_unpublished_recent_day():
    today = date(2026, 10, 10)  # Saturday
    published = {date(2026, 10, 6): True, date(2026, 10, 7): True, date(2026, 10, 8): False,
                 date(2026, 10, 9): True}
    assert next_pointer(date(2026, 10, 5), published, today) == date(2026, 10, 7)


def test_pointer_skips_old_holidays_and_weekends():
    today = date(2026, 12, 30)
    published = {date(2026, 12, 25): False,   # holiday, 5 days old
                 date(2026, 12, 26): False,   # Saturday
                 date(2026, 12, 27): False,   # Sunday
                 date(2026, 12, 28): True}
    assert next_pointer(date(2026, 12, 24), published, today) == date(2026, 12, 28)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_first_scan_only_sets_pointer(conn, tmp_path):
    client, session = fake_client(tmp_path)
    assert scan_daily_indexes(conn, client, {320193}, date(2026, 10, 10)) == {}
    assert get_state(conn, POINTER_KEY) == "2026-10-09"
    assert session.calls == []


def test_scan_collects_forms_for_tracked_ciks(conn, tmp_path):
    set_state(conn, POINTER_KEY, "2026-10-05")

    def handler(url, params):
        if url.endswith("master.20261006.idx"):
            return FakeResponse(content=MASTER)
        if url.endswith("master.20261009.idx"):
            return FakeResponse(404)  # not published yet
        return FakeResponse(content=MASTER.split("--------")[0])  # published, no rows we track

    client, _ = fake_client(tmp_path, handler=handler)
    changed = scan_daily_indexes(conn, client, {320193, 1065280}, date(2026, 10, 10))
    assert changed == {320193: {"10-Q"}, 1065280: {"4"}}
    assert get_state(conn, POINTER_KEY) == "2026-10-08"
