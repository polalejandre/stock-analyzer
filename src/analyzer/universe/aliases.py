"""Identity of past index members: renames (aliases), why others are excluded (fates), and
tickers that now belong to a different company (recycled)."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from analyzer.data.edgar.merges import DEFAULT_PATH as MERGES_PATH
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.http import HttpClient
from analyzer.store.db import get_state, set_state
from analyzer.universe.wikipedia import parse_changes, parse_constituents, revision_html

log = logging.getLogger(__name__)

MANUAL_PATH = Path(__file__).with_name("aliases.toml")
PARSED_REVISIONS_KEY = "snapshot_cik_revisions"  # sync_state: JSON list of revids already parsed
ACQUIRED = re.compile(r"\b(acquir\w*|merg\w*|bought|takeover|taken private|buyout)\b", re.I)


@dataclass
class AliasSummary:
    aliases: int = 0
    recycled: int = 0
    excluded: dict[str, int] = field(default_factory=dict)


def backfill_snapshot_ciks(conn, client: HttpClient) -> int:
    """Fill index_snapshots.cik from each revision's own table. Revisions are cached on disk by
    the snapshot sync, so this reads the cache; each revision is parsed once."""
    done = set(json.loads(get_state(conn, PARSED_REVISIONS_KEY) or "[]"))
    revids = [r[0] for r in conn.execute(
        "SELECT DISTINCT revision_id FROM index_snapshots WHERE cik IS NULL")]
    updated = 0
    for revid in revids:
        if revid in done:
            continue
        try:
            df = parse_constituents(revision_html(client, revid))
        except Exception as exc:  # an unreadable revision only leaves its CIKs empty
            log.warning("revision %s: %s: %s", revid, type(exc).__name__, exc)
            continue
        pairs = [(int(c), revid, t) for t, c in zip(df.ticker, df.cik, strict=True) if c is not None]
        with conn:
            conn.executemany("UPDATE index_snapshots SET cik = ? WHERE revision_id = ? AND ticker = ?",
                             pairs)
        updated += len(pairs)
        done.add(revid)
    set_state(conn, PARSED_REVISIONS_KEY, json.dumps(sorted(done)))
    return updated


def snapshot_ciks(conn) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for ticker, cik in conn.execute(
            "SELECT DISTINCT ticker, cik FROM index_snapshots WHERE cik IS NOT NULL"):
        out.setdefault(ticker, set()).add(int(cik))
    return out


def change_reasons(conn, client: HttpClient) -> dict[str, str]:
    """Removed ticker -> reason, from the latest stored revision of each index's article."""
    out: dict[str, str] = {}
    for (revid,) in conn.execute(
            "SELECT revision_id FROM index_snapshots s WHERE snapshot_date = "
            "(SELECT MAX(snapshot_date) FROM index_snapshots WHERE index_name = s.index_name) "
            "GROUP BY index_name").fetchall():
        try:
            df = parse_changes(revision_html(client, revid))
        except Exception as exc:
            log.warning("changes table of revision %s: %s: %s", revid, type(exc).__name__, exc)
            continue
        for t, reason in zip(df.removed_ticker, df.reason, strict=True):
            if reason:
                out.setdefault(t, reason)
    return out


def classify_fate(reason: str | None) -> str:
    if not reason:
        return "unknown"
    return "acquired" if ACQUIRED.search(reason) else "other"


def _load_manual(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    data = tomllib.loads(Path(path).read_text())
    return {e["ticker"]: e["price_symbol"] for e in data.get("alias", [])}


def _canonical_map(conn, merges_path: Path) -> dict[int, int]:
    """Predecessor CIK -> the current CIK its history belongs to."""
    rows = conn.execute("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL").fetchall()
    if not rows:
        return {}
    ticker_map = pd.DataFrame(rows, columns=["ticker", "cik"])
    return {p: cur for cur, preds in load_cik_merges(ticker_map, merges_path).items()
            for p in preds}


def build_aliases(conn, reasons: Mapping[str, str], manual_path: Path = MANUAL_PATH,
                  merges_path: Path = MERGES_PATH) -> AliasSummary:
    """Rewrite ticker_aliases and member_fates for every ticker ever in an index."""
    priced = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices")}
    current_cik = {t: c for t, c in conn.execute("SELECT ticker, cik FROM tickers") if c is not None}
    members = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM index_membership")}
    symbol_by_cik: dict[int, str] = {}
    for t in sorted(priced):  # deterministic: alphabetical first symbol per CIK
        if t in current_cik:
            symbol_by_cik.setdefault(int(current_cik[t]), t)
    canon_of = _canonical_map(conn, merges_path)
    snaps = {t: {canon_of.get(c, c) for c in ciks} for t, ciks in snapshot_ciks(conn).items()}
    manual = _load_manual(manual_path)

    aliases: list[tuple] = []
    fates: list[tuple] = []
    for t in sorted(members):
        seen = snaps.get(t, set())
        if t in manual:
            aliases.append((t, manual[t], current_cik.get(manual[t]), "manual"))
        elif len(seen) > 1:  # the ticker named several companies over time
            fates.append((t, "recycled", f"snapshot CIKs {sorted(seen)} name several companies"))
        else:
            match = next((c for c in sorted(seen) if c in symbol_by_cik), None)
            target = symbol_by_cik[match] if match is not None else None
            if t in priced:
                cur = current_cik.get(t)
                if seen and cur is not None and int(cur) not in seen:
                    if target is not None and target != t:
                        aliases.append((t, target, match, "cik"))
                    else:
                        fates.append((t, "recycled", f"snapshot CIK {sorted(seen)} != price CIK {cur}"))
            elif target is not None:
                aliases.append((t, target, match, "cik"))
            else:
                reason = reasons.get(t)
                fates.append((t, classify_fate(reason), reason))
    with conn:
        conn.execute("DELETE FROM ticker_aliases")
        conn.execute("DELETE FROM member_fates")
        conn.executemany("INSERT INTO ticker_aliases VALUES (?, ?, ?, ?)", aliases)
        conn.executemany("INSERT INTO member_fates VALUES (?, ?, ?)", fates)
    counts = Counter(f for _, f, _ in fates)
    recycled = counts.pop("recycled", 0)
    return AliasSummary(aliases=len(aliases), recycled=recycled, excluded=dict(counts))


def sync_aliases(conn, client: HttpClient) -> AliasSummary:
    backfill_snapshot_ciks(conn, client)
    return build_aliases(conn, change_reasons(conn, client))
