"""Store daily prices and keep them current with the fewest downloads.

Storage rules that keep adjusted and unadjusted data from mixing:
- `close` is split-adjusted, `adj_close` is split- and dividend-adjusted; only the primary
  provider supplies the latter. Fallback rows get an `adj_close` derived from the latest
  stored adjustment ratio, and a fallback only appends bars newer than the last stored one
  (refreshing that last bar, which may be partial, when it does); it never rewrites older bars.
- A split or dividend rewrites the whole adjusted history, so when overlapping days no longer
  match what is stored, the symbol's full history is re-downloaded from the primary and
  replaced. So is history that was built from a fallback once the primary serves the symbol.
- A full replacement must cover the stored range; a truncated response never replaces history.
  A refused heal does not hold the symbol back: its new bars are written as usual and the heal
  is not attempted again for HEAL_RETRY_DAYS.
- The `splits` table lists the split events the stored closes are adjusted for. It is rewritten
  together with every full history the primary supplies (new, stale, adjustment-change refetch,
  heal), because that is when the adjustment basis of the stored closes changes. Fallback
  histories carry no split events and leave it alone.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.prices import PRICE_COLUMNS, PriceProvider, Splits, fetch_with_fallback
from analyzer.store.db import get_state, set_state, upsert_rows

HISTORY_START = date(2009, 1, 1)
OVERLAP_DAYS = 14
ADJ_TOLERANCE = 1e-4
STALE_AFTER_DAYS = 365
COVERAGE_GRACE_DAYS = 10  # a replacement may start this long after the stored history does
COVERAGE_MIN_RATIO = 0.9  # and must hold this share of the stored bars it spans
MISSES_KEY = "price_historical_misses"  # sync_state: JSON {symbol: iso date of the last miss}
MISSES_RETRY_DAYS = 30
HEAL_REFUSED_KEY = "price_heal_refused"  # sync_state: JSON {symbol: iso date a heal was refused}
HEAL_RETRY_DAYS = 30
FULL_CHUNK_SIZE = 200  # full histories are fetched and stored this many symbols at a time

_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
_NUMERIC = ["open", "high", "low", "close", "adj_close", "volume"]


def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _clean(v):
    return None if v is None or pd.isna(v) else float(v)


def _usable(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows with a close price; `close` is NOT NULL in storage. Providers already drop the rest."""
    return frame.dropna(subset=["close"])


def _rows(symbol: str, frame: pd.DataFrame, source: str):
    for r in _usable(frame).itertuples(index=False):
        yield (symbol, _iso(r.date), _clean(r.open), _clean(r.high), _clean(r.low),
               float(r.close), _clean(r.adj_close), _clean(r.volume), source)


def read_prices(conn: sqlite3.Connection, symbol: str, start=None, end=None) -> pd.DataFrame:
    sql = ("SELECT date, open, high, low, close, adj_close, volume, source "
           "FROM prices WHERE symbol = ?")
    params: list = [symbol]
    if start is not None:
        sql += " AND date >= ?"
        params.append(_iso(start))
    if end is not None:
        sql += " AND date <= ?"
        params.append(_iso(end))
    df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
    # Same dtypes as provider frames whatever the result (empty, all-NULL column, pandas default unit).
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    for col in _NUMERIC:
        df[col] = df[col].astype("float64")
    return df


def write_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    upsert_rows(conn, "prices", _COLUMNS, _rows(symbol, frame, source))


def replace_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    rows = list(_rows(symbol, frame, source))  # build first: a bad frame must not touch the table
    if not rows:
        raise ValueError(f"no usable price rows to replace {symbol} with")
    with conn:
        conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))
        conn.executemany(
            f"INSERT OR REPLACE INTO prices ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            rows,
        )


def replace_splits(conn, symbol: str, splits: Sequence[tuple[date, float]]) -> None:
    """Make `splits` the symbol's complete list of split events."""
    with conn:
        conn.execute("DELETE FROM splits WHERE symbol = ?", (symbol,))
        conn.executemany("INSERT OR REPLACE INTO splits (symbol, date, ratio) VALUES (?, ?, ?)",
                         [(symbol, _iso(d), float(r)) for d, r in splits])


def read_splits(conn, symbol: str) -> list[tuple[date, float]]:
    return [(date.fromisoformat(d), r) for d, r in conn.execute(
        "SELECT date, ratio FROM splits WHERE symbol = ? ORDER BY date", (symbol,))]


def _store_splits(conn, symbol: str, splits: Splits) -> None:
    """After a full replacement: record the provider's split events, if it reported any."""
    if symbol in splits:
        replace_splits(conn, symbol, splits[symbol])


def last_dates(conn, symbols: Sequence[str]) -> dict[str, date]:
    wanted = set(symbols)
    rows = conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol").fetchall()
    return {s: date.fromisoformat(d) for s, d in rows if s in wanted}


def latest_market_date(conn) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM prices WHERE symbol = 'SPY'").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _latest_ratio(conn, symbol: str) -> float:
    """adj_close / close of the latest stored bar, or 1.0 when nothing usable is stored."""
    row = conn.execute(
        "SELECT close, adj_close FROM prices WHERE symbol = ? AND adj_close IS NOT NULL "
        "AND close > 0 ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return row[1] / row[0] if row else 1.0


def _with_adj(frame: pd.DataFrame, ratio: float = 1.0) -> pd.DataFrame:
    """Fill missing adj_close as close x ratio (fallback providers have no dividend adjustment)."""
    if not frame["adj_close"].isna().any():
        return frame
    frame = frame.copy()
    frame["adj_close"] = frame["adj_close"].fillna(frame["close"] * ratio)
    return frame


def _drift(new: pd.Series, old: pd.Series) -> float:
    """Largest relative change between matching values; NaN and non-positive olds are ignored."""
    return ((new / old.where(old > 0)) - 1).abs().max()


def _overlap(stored: pd.DataFrame, fresh: pd.DataFrame, source: str | None = None) -> pd.DataFrame:
    """Stored bars paired by date with fresh ones, minus the most recent stored day (may be partial)."""
    stored = stored[stored["date"] < stored["date"].max()]
    if source is not None:  # only primary rows carry true adjusted values
        stored = stored[stored["source"] == source]
    return stored.merge(fresh[PRICE_COLUMNS], on="date", suffixes=("_old", "_new"))


def _drifted(merged: pd.DataFrame, columns: Sequence[str]) -> bool:
    return any(_drift(merged[f"{c}_new"], merged[f"{c}_old"]) > ADJ_TOLERANCE for c in columns)


def _adjustment_changed(stored: pd.DataFrame, fresh: pd.DataFrame, primary: str) -> bool:
    """True when overlapping days from the primary no longer match what is stored."""
    return _drifted(_overlap(stored, fresh, primary), ("close", "adj_close"))


def _close_changed(stored: pd.DataFrame, fresh: pd.DataFrame) -> bool:
    """True when overlapping closes differ from every stored bar, whatever its source (e.g. a split)."""
    return _drifted(_overlap(stored, fresh), ("close",))


def _has_fallback_history(conn, symbol: str, primary: str, before: date) -> bool:
    row = conn.execute(
        "SELECT 1 FROM prices WHERE symbol = ? AND source <> ? AND date < ? LIMIT 1",
        (symbol, primary, _iso(before)),
    ).fetchone()
    return row is not None


def _covers_stored(conn, symbol: str, frame: pd.DataFrame) -> bool:
    """A replacement must span the stored history; otherwise it is a truncated response."""
    (first,) = conn.execute("SELECT MIN(date) FROM prices WHERE symbol = ?", (symbol,)).fetchone()
    if first is None:
        return True
    new_first = frame["date"].min()
    if new_first > pd.Timestamp(first) + pd.Timedelta(days=COVERAGE_GRACE_DAYS):
        return False
    (spanned,) = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE symbol = ? AND date >= ?", (symbol, _iso(new_first))
    ).fetchone()
    return frame["date"].nunique() >= COVERAGE_MIN_RATIO * spanned


def _replace_checked(conn, symbol: str, frame: pd.DataFrame, source: str) -> bool:
    """Replace stored history with `frame`; False (nothing touched) if it is unusable or truncated."""
    frame = _usable(frame)
    if frame.empty or not _covers_stored(conn, symbol, frame):
        return False
    replace_prices(conn, symbol, _with_adj(frame), source)  # replaced rows are gone: no stored ratio
    return True


def _load_dates(conn, key: str) -> dict[str, date]:
    """A {symbol: date} map kept in sync_state; unreadable state or entries only cost a retry."""
    try:
        raw = json.loads(get_state(conn, key) or "{}")
    except ValueError:
        return {}
    out: dict[str, date] = {}
    for sym, day in (raw.items() if isinstance(raw, dict) else ()):
        try:
            out[sym] = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
    return out


def _save_dates(conn, key: str, dates: dict[str, date]) -> None:
    set_state(conn, key, json.dumps({s: d.isoformat() for s, d in dates.items()}))


def _recent(dates: dict[str, date], today: date, days: int) -> set[str]:
    return {s for s, d in dates.items() if today - d < timedelta(days=days)}


@dataclass
class PriceSyncReport:
    updated: list[str] = field(default_factory=list)
    refetched: list[str] = field(default_factory=list)
    via_fallback: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    historical_missing: list[str] = field(default_factory=list)


def _sync_full(conn, providers, full, active_set, today, history_start, report) -> None:
    """Full history for symbols never stored (or active and stale). Past index members that no
    provider returned are not asked for again for MISSES_RETRY_DAYS."""
    primary = providers[0]
    misses = _load_dates(conn, MISSES_KEY)
    recent = _recent(misses, today, MISSES_RETRY_DAYS)
    skipped = [s for s in full if s not in active_set and s in recent]
    to_fetch = [s for s in full if s not in skipped]

    missing: list[str] = []
    # Chunk by chunk, each written before the next is fetched: an interrupted first run keeps
    # what it finished, and the rerun only asks for the symbols still not stored.
    for i in range(0, len(to_fetch), FULL_CHUNK_SIZE):
        res = fetch_with_fallback(providers, to_fetch[i : i + FULL_CHUNK_SIZE], history_start, today)
        missing += res.missing
        for sym, frame in res.frames.items():
            if not _replace_checked(conn, sym, frame, res.sources[sym]):
                missing.append(sym)
                continue
            _store_splits(conn, sym, res.splits)
            report.updated.append(sym)
            if res.sources[sym] != primary.name:
                report.via_fallback[sym] = res.sources[sym]
    report.missing += [s for s in missing if s in active_set]
    newly_missed = [s for s in missing if s not in active_set]
    report.historical_missing += newly_missed + skipped

    fetched = set(to_fetch)
    kept = {s: d for s, d in misses.items() if s in recent and s not in fetched}
    updated_misses = kept | {s: today for s in newly_missed}
    if updated_misses != misses:
        _save_dates(conn, MISSES_KEY, updated_misses)


def _append(conn, sym: str, frame: pd.DataFrame, source: str, primary: str, report) -> None:
    write_prices(conn, sym, _with_adj(frame, _latest_ratio(conn, sym)), source)
    report.updated.append(sym)
    if source != primary:
        report.via_fallback[sym] = source


def _sync_incremental(conn, providers, incremental, known, today, no_heal, report):
    """Append new days. Returns (symbols whose adjusted history changed and must be re-downloaded,
    {symbol: its primary window} for symbols whose fallback-built history should be healed)."""
    primary = providers[0]
    groups: dict[date, list[str]] = {}
    for sym in incremental:  # one fetch per distinct window start: a stuck symbol widens only its own
        groups.setdefault(known[sym] - timedelta(days=OVERLAP_DAYS), []).append(sym)

    stale: list[str] = []
    healing: dict[str, pd.DataFrame] = {}
    for start in sorted(groups):
        res = fetch_with_fallback(providers, groups[start], start, today)
        report.missing += res.missing
        for sym, frame in res.frames.items():
            frame = _usable(frame)
            source = res.sources[sym]
            if frame.empty:
                report.missing.append(sym)
                continue
            stored = read_prices(conn, sym, start=frame["date"].min())
            if source == primary.name:
                if _adjustment_changed(stored, frame, primary.name):
                    stale.append(sym)
                    continue
                if sym not in no_heal and _has_fallback_history(conn, sym, primary.name, start):
                    healing[sym] = frame
                    continue
            else:
                if _close_changed(stored, frame):  # e.g. a split while the primary was down
                    report.missing.append(sym)
                    continue
                last = pd.Timestamp(known[sym])
                if frame[frame["date"] > last].empty:  # nothing new: leave the stored bars alone
                    continue
                frame = frame[frame["date"] >= last]
            _append(conn, sym, frame, source, primary.name, report)
    return stale, healing


def sync_prices(
    conn,
    providers: Sequence[PriceProvider],
    active: Sequence[str],
    historical: Sequence[str],
    today: date,
    history_start: date = HISTORY_START,
) -> PriceSyncReport:
    primary = providers[0]
    report = PriceSyncReport()
    active = list(dict.fromkeys(active))
    active_set = set(active)
    everything = list(dict.fromkeys([*active, *historical]))
    known = last_dates(conn, everything)
    stale_cutoff = today - timedelta(days=STALE_AFTER_DAYS)

    full = [s for s in everything
            if s not in known or (s in active_set and known[s] < stale_cutoff)]
    incremental = [s for s in active if s in known and s not in full]

    if full:
        _sync_full(conn, providers, full, active_set, today, history_start, report)
    refused = _load_dates(conn, HEAL_REFUSED_KEY)
    blocked = _recent(refused, today, HEAL_RETRY_DAYS)
    stale: list[str] = []
    healing: dict[str, pd.DataFrame] = {}
    if incremental:
        stale, healing = _sync_incremental(conn, providers, incremental, known, today, blocked, report)

    refetched: set[str] = set()
    refused_now: list[str] = []
    if stale or healing:
        res = fetch_with_fallback([primary], [*stale, *healing], history_start, today)
        got = res.frames
        for sym in stale:
            if sym in got and _replace_checked(conn, sym, got[sym], primary.name):
                _store_splits(conn, sym, res.splits)
                refetched.add(sym)
            else:
                report.missing.append(sym)  # stored rows stay exactly as they were
        for sym, window in healing.items():
            if sym in got and _replace_checked(conn, sym, got[sym], primary.name):
                _store_splits(conn, sym, res.splits)
                refetched.add(sym)
                continue
            if sym in got:  # the primary's history is too short to replace the stored one
                refused_now.append(sym)
            # Either way the symbol carries on as a normal incremental update.
            _append(conn, sym, window, primary.name, primary.name, report)
        report.refetched += [s for s in [*stale, *healing] if s in refetched]

    kept = {s: d for s, d in refused.items() if s in blocked and s not in refetched}
    new_refused = kept | {s: today for s in refused_now}
    if new_refused != refused:
        _save_dates(conn, HEAL_REFUSED_KEY, new_refused)
    return report
