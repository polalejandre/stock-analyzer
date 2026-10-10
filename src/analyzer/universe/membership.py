"""Index membership intervals rebuilt from periodic constituent snapshots."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

INDEXES = ("sp500", "sp400")


@dataclass(frozen=True)
class Interval:
    ticker: str
    start: date
    end: date | None  # exclusive; None = still a member


def build_membership(
    snapshots: Sequence[tuple[date, set[str]]], date_added: Mapping[str, date] | None = None
) -> list[Interval]:
    """A ticker is a member from the first snapshot that lists it until the first that doesn't.

    `date_added` (from the current page) moves a start date earlier when it falls between the
    previous snapshot and the one where the ticker first appears.
    """
    date_added = date_added or {}
    open_since: dict[str, date] = {}
    out: list[Interval] = []
    prev: date | None = None
    for d, members in sorted(snapshots, key=lambda s: s[0]):
        for ticker in members - open_since.keys():
            start = d
            added = date_added.get(ticker)
            if added and prev and prev < added <= d:
                start = added
            open_since[ticker] = start
        for ticker in open_since.keys() - members:
            out.append(Interval(ticker, open_since.pop(ticker), d))
        prev = d
    out += [Interval(t, s, None) for t, s in open_since.items()]
    return sorted(out, key=lambda i: (i.ticker, i.start))


def rebuild_membership(conn, index_name: str) -> int:
    snaps: dict[date, set[str]] = defaultdict(set)
    added: dict[str, date] = {}
    rows = conn.execute(
        "SELECT snapshot_date, ticker, date_added FROM index_snapshots WHERE index_name = ? "
        "ORDER BY snapshot_date",
        (index_name,),
    ).fetchall()
    for snap_date, ticker, date_added in rows:
        snaps[date.fromisoformat(snap_date)].add(ticker)
        if date_added:
            added[ticker] = date.fromisoformat(date_added)
    intervals = build_membership(list(snaps.items()), added)
    with conn:
        conn.execute("DELETE FROM index_membership WHERE index_name = ?", (index_name,))
        conn.executemany(
            "INSERT INTO index_membership VALUES (?, ?, ?, ?)",
            [(index_name, i.ticker, i.start.isoformat(), i.end.isoformat() if i.end else None)
             for i in intervals],
        )
    return len(intervals)


def members_on(conn, index_name: str, d: date) -> set[str]:
    rows = conn.execute(
        "SELECT ticker FROM index_membership WHERE index_name = ? AND start_date <= ? "
        "AND (end_date IS NULL OR end_date > ?)",
        (index_name, d.isoformat(), d.isoformat()),
    )
    return {r[0] for r in rows}
