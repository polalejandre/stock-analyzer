"""S&P 500/400 constituents from the current and past revisions of the Wikipedia lists."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.http import HttpClient
from analyzer.universe.membership import rebuild_membership

API_URL = "https://en.wikipedia.org/w/api.php"
INDEX_URL = "https://en.wikipedia.org/w/index.php"
PAGES = {"sp500": "List_of_S&P_500_companies", "sp400": "List_of_S&P_400_companies"}
EXPECTED_SIZE = {"sp500": (480, 520), "sp400": (380, 420)}  # sanity bounds per snapshot
SNAPSHOT_START = {"sp500": date(2010, 1, 1), "sp400": date(2013, 1, 1)}


@dataclass(frozen=True)
class Revision:
    revid: int
    timestamp: str


def normalize_ticker(raw) -> str | None:
    t = re.sub(r"\[.*?\]", "", str(raw)).strip().upper().replace(".", "-").replace(" ", "")
    return t if re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", t) else None


def _find(cols: dict[str, object], *needles: str):
    return next((c for k, c in cols.items() if any(n in k for n in needles)), None)


def _text_column(table: pd.DataFrame, column):
    if column is None:
        return None
    return table[column].astype(str).where(table[column].notna(), None)


def parse_constituents(html: str) -> pd.DataFrame:
    """The first table with >= 50 rows and a symbol/ticker column, in a fixed shape."""
    for t in pd.read_html(io.StringIO(html), flavor="lxml"):
        if len(t) < 50 or isinstance(t.columns, pd.MultiIndex):
            continue
        cols = {str(c).lower(): c for c in t.columns}
        sym = _find(cols, "symbol", "ticker")
        if sym is None:
            continue
        sec = _find(cols, "security", "company")
        sector = _find(cols, "gics sector")
        sub = _find(cols, "gics sub")
        added = _find(cols, "date added", "date first added")
        out = pd.DataFrame(
            {
                "ticker": t[sym].map(normalize_ticker),
                "security": _text_column(t, sec),
                "gics_sector": _text_column(t, sector),
                "gics_sub_industry": _text_column(t, sub),
                "date_added": (
                    pd.to_datetime(t[added], errors="coerce").dt.strftime("%Y-%m-%d")
                    if added is not None else None
                ),
            }
        )
        out["date_added"] = out["date_added"].where(out["date_added"].notna(), None)
        return out.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)
    raise ValueError("no constituents table found")


def revisions_before(
    client: HttpClient, title: str, when: date, today: date, limit: int = 5
) -> list[Revision]:
    final = when <= today - timedelta(days=2)  # older answers can no longer change
    data = client.get_json(
        API_URL,
        params={"action": "query", "prop": "revisions", "titles": title, "rvlimit": limit,
                "rvstart": f"{when.isoformat()}T00:00:00Z", "rvdir": "older",
                "rvprop": "ids|timestamp", "format": "json", "formatversion": 2},
        cache_key=f"wiki/revisions/{title.replace('&', 'and')}/{when.isoformat()}",
        max_age=None if final else timedelta(hours=12),
    )
    revs = data["query"]["pages"][0].get("revisions", [])
    return [Revision(int(r["revid"]), r["timestamp"]) for r in revs]


def revision_html(client: HttpClient, revid: int) -> str:
    return client.get_text(INDEX_URL, params={"oldid": revid}, cache_key=f"wiki/html/{revid}.html")


def snapshot_for(client, index_name: str, when: date, today: date) -> tuple[Revision, pd.DataFrame]:
    lo, hi = EXPECTED_SIZE[index_name]
    for rev in revisions_before(client, PAGES[index_name], when, today):
        try:
            df = parse_constituents(revision_html(client, rev.revid))
        except ValueError:
            continue
        if lo <= len(df) <= hi:
            return rev, df
    raise ValueError(f"no sane {index_name} revision before {when}")


def month_starts(start: date, end: date) -> list[date]:
    d = start if start.day == 1 else (start.replace(day=1) + timedelta(days=32)).replace(day=1)
    out = []
    while d <= end:
        out.append(d)
        d = (d + timedelta(days=32)).replace(day=1)
    return out


@dataclass
class SnapshotSync:
    attempted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def sync_index_snapshots(
    conn, client: HttpClient, index_name: str, today: date, start: date | None = None
) -> SnapshotSync:
    start = start or SNAPSHOT_START[index_name]
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM index_snapshots WHERE index_name = ?", (index_name,))}
    targets = [d for d in month_starts(start, today) if d.isoformat() not in have]
    if today.day != 1:
        targets.append(today)
    result = SnapshotSync()
    for d in targets:
        result.attempted += 1
        when = d + timedelta(days=1) if d == today else d  # today's snapshot = latest revision
        try:
            rev, df = snapshot_for(client, index_name, when, today)
        except Exception as exc:  # one bad month must not stop the rest
            result.failed.append((f"{index_name} {d}", f"{type(exc).__name__}: {exc}"))
            continue
        rows = [(index_name, d.isoformat(), rev.revid, r.ticker, r.security, r.gics_sector,
                 r.gics_sub_industry, r.date_added) for r in df.itertuples()]
        with conn:
            conn.execute(
                "DELETE FROM index_snapshots WHERE index_name = ? AND snapshot_date = ?",
                (index_name, d.isoformat()),
            )
            conn.executemany("INSERT INTO index_snapshots VALUES (?,?,?,?,?,?,?,?)", rows)
    with conn:
        conn.execute(
            "DELETE FROM index_snapshots WHERE index_name = ? AND substr(snapshot_date, 9, 2) <> '01' "
            "AND snapshot_date <> ?",
            (index_name, today.isoformat()),
        )
    rebuild_membership(conn, index_name)
    return result
