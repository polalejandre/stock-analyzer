from datetime import date

from analyzer.store.db import connect, migrate
from analyzer.universe.membership import Interval, build_membership, members_on

D = date


def test_additions_and_removals():
    snaps = [
        (D(2020, 1, 1), {"A", "B"}),
        (D(2020, 2, 1), {"A", "C"}),
        (D(2020, 3, 1), {"A", "C"}),
    ]
    assert build_membership(snaps) == [
        Interval("A", D(2020, 1, 1), None),
        Interval("B", D(2020, 1, 1), D(2020, 2, 1)),
        Interval("C", D(2020, 2, 1), None),
    ]


def test_date_added_refines_start_within_gap():
    snaps = [(D(2020, 1, 1), {"A"}), (D(2020, 2, 1), {"A", "C"})]
    out = build_membership(snaps, {"C": D(2020, 1, 15), "A": D(1990, 1, 1)})
    assert Interval("C", D(2020, 1, 15), None) in out
    assert Interval("A", D(2020, 1, 1), None) in out  # outside the gap: ignored


def test_readded_ticker_gets_two_intervals():
    snaps = [(D(2020, 1, 1), {"X"}), (D(2020, 2, 1), set()), (D(2020, 3, 1), {"X"})]
    assert build_membership(snaps) == [
        Interval("X", D(2020, 1, 1), D(2020, 2, 1)),
        Interval("X", D(2020, 3, 1), None),
    ]


def test_members_on(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO index_membership VALUES (?,?,?,?)",
        [("sp500", "A", "2020-01-01", None), ("sp500", "B", "2020-01-01", "2020-02-01")],
    )
    assert members_on(conn, "sp500", D(2020, 1, 31)) == {"A", "B"}
    assert members_on(conn, "sp500", D(2020, 2, 1)) == {"A"}
    assert members_on(conn, "sp400", D(2020, 2, 1)) == set()
