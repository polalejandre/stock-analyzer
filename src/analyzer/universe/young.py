"""Find liquid stocks that listed recently and are not in the S&P 500/400."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Collection
from datetime import date, timedelta

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import HISTORY_START, replace_prices
from analyzer.data.prices import PriceProvider, fetch_frames_and_splits
from analyzer.store.db import get_state, set_state, upsert_rows
from analyzer.universe.members import MAX_BAR_AGE_DAYS

LISTED_EXCHANGES = {"NYSE", "Nasdaq"}
_NASDAQ_DERIVATIVE = re.compile(r"[A-Z]{4}[WUR]")          # warrants, units, rights
_SUFFIX_DERIVATIVE = re.compile(r"-(P[A-Z]?|WS?|WT|U|UN|R|RT)$")  # preferreds, warrants, units
SCREEN_WINDOW_DAYS = 45  # floor; longer liquidity windows widen it (see screen_window_days)
CLIPPED_MARGIN = timedelta(days=30)
VERDICTS_KEY = "young_screen_verdicts"  # sync_state: JSON {ticker: {"verdict", "on"}}
VERDICT_RECHECK_DAYS = 180  # a settled ticker is screened again after this long (ticker reuse)
SETTLED_VERDICTS = ("old", "secondary")


def young_candidates(
    ticker_map: pd.DataFrame, exclude: Collection[str] = frozenset()
) -> list[str]:
    tm = ticker_map[ticker_map["exchange"].isin(LISTED_EXCHANGES)]
    keep = [
        t for t in tm["ticker"]
        if t not in exclude
        and not (len(t) == 5 and _NASDAQ_DERIVATIVE.fullmatch(t))
        and not _SUFFIX_DERIVATIVE.search(t)
    ]
    return sorted(set(keep))


def _usable(frame: pd.DataFrame) -> pd.DataFrame:
    """Bars in date order that storage would keep (a close price is required)."""
    return frame.dropna(subset=["close"]).sort_values("date")


def average_dollar_volume(frame: pd.DataFrame, days: int) -> float:
    """Mean close x volume of the last `days` bars; a missing volume counts as 0, as in storage."""
    tail = _usable(frame).tail(days)
    return float((tail["close"] * tail["volume"].fillna(0.0)).mean())


def _cik_by_ticker(ticker_map: pd.DataFrame) -> dict[str, int]:
    """Tickers with a usable CIK. A listing without one can never get a market cap."""
    return {t: int(c) for t, c in zip(ticker_map["ticker"], ticker_map["cik"], strict=True)
            if pd.notna(c)}


def _primary_ticker(ticker_map: pd.DataFrame) -> dict[str, str]:
    """{ticker: the first ticker SEC lists for the same CIK}, i.e. the company's main listing."""
    first = ticker_map.drop_duplicates("cik").set_index("cik")["ticker"]
    return {t: first[c] for t, c in zip(ticker_map["ticker"], ticker_map["cik"], strict=True)
            if pd.notna(c)}


def screen_window_days(rules: UniverseRules) -> int:
    """Calendar days of prices that cover the liquidity window (5 of 7 days trade) plus slack."""
    return max(SCREEN_WINDOW_DAYS, math.ceil(rules.dollar_volume_days * 7 / 5) + 15)


def _current_and_liquid(frame: pd.DataFrame, rules: UniverseRules, today: date) -> bool:
    bars = _usable(frame)
    if len(bars) < rules.dollar_volume_days:
        return False
    if (today - bars["date"].max().date()).days > MAX_BAR_AGE_DAYS:
        return False  # delisted or stale: not tradable now
    return average_dollar_volume(bars, rules.dollar_volume_days) >= rules.min_dollar_volume


def _first_bars(conn, ticker: str, primary: str) -> tuple[date | None, date | None]:
    """(first stored bar from the primary provider, first stored bar from any other source)."""
    first_primary, first_other = conn.execute(
        "SELECT MIN(CASE WHEN source = ? THEN date END), MIN(CASE WHEN source <> ? THEN date END) "
        "FROM prices WHERE symbol = ?", (primary, primary, ticker)).fetchone()
    return (date.fromisoformat(first_primary) if first_primary else None,
            date.fromisoformat(first_other) if first_other else None)


def _primary_first_date(first_primary: date | None, first_other: date | None) -> date | None:
    """The listing date the stored history supports, or None when it cannot date the listing:
    rows from a fallback reach back further than the primary's, i.e. the history was built from
    a fallback that may serve only the last few years (the price sync heals it later)."""
    if first_primary is None or (first_other is not None and first_other < first_primary):
        return None
    return first_primary


def revalidate_young_listings(conn, primary: str, history_start: date = HISTORY_START) -> None:
    """Re-date stored young listings from their primary-provider history: a listing whose first
    primary bar is now within the clipped margin (e.g. a fallback-built history was healed) is
    deleted, and one whose first primary bar moved is re-dated. A listing whose history cannot be
    judged (no primary rows, or older fallback rows still in it) is left as it is."""
    deleted, moved = [], []
    for ticker, first_trade in conn.execute(
            "SELECT ticker, first_trade_date FROM young_listings").fetchall():
        first = _primary_first_date(*_first_bars(conn, ticker, primary))
        if first is None:
            continue
        if first <= history_start + CLIPPED_MARGIN:
            deleted.append((ticker,))
        elif first.isoformat() != first_trade:
            moved.append((first.isoformat(), ticker))
    with conn:
        conn.executemany("DELETE FROM young_listings WHERE ticker = ?", deleted)
        conn.executemany("UPDATE young_listings SET first_trade_date = ? WHERE ticker = ?", moved)


def _load_verdicts(conn) -> dict[str, tuple[str, date]]:
    """{ticker: (verdict, date judged)}; unreadable state or entries only cost a re-check."""
    try:
        raw = json.loads(get_state(conn, VERDICTS_KEY) or "{}")
    except ValueError:
        return {}
    out: dict[str, tuple[str, date]] = {}
    for ticker, entry in (raw.items() if isinstance(raw, dict) else ()):
        try:
            out[ticker] = (str(entry["verdict"]), date.fromisoformat(entry["on"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save_verdicts(conn, verdicts: dict[str, tuple[str, date]], today: date) -> None:
    keep = {t: {"verdict": v, "on": d.isoformat()} for t, (v, d) in sorted(verdicts.items())
            if (today - d).days < VERDICT_RECHECK_DAYS}
    set_state(conn, VERDICTS_KEY, json.dumps(keep))


def screen_young_listings(
    conn,
    primary: PriceProvider,
    ticker_map: pd.DataFrame,
    rules: UniverseRules,
    today: date,
    history_start: date = HISTORY_START,
) -> list[str]:
    """Record liquid, currently trading listings that began trading after the history window.

    Index members are screened too: a stock that IPO'd recently and later joined an index was
    a young listing while it was young. Tickers whose prices are already stored are not
    downloaded or rewritten; their first stored bar from the primary provider dates the listing,
    and a stored history that a fallback reaches back further in is not used this week.
    Tickers judged "old" (history reaching back to the start) or "secondary" (a newer listing
    under an old company's CIK) are not screened again for VERDICT_RECHECK_DAYS, so their full
    histories are not downloaded every week.
    """
    revalidate_young_listings(conn, primary.name, history_start)
    verdicts = _load_verdicts(conn)
    settled = {t for t, (v, d) in verdicts.items()
               if v in SETTLED_VERDICTS and (today - d).days < VERDICT_RECHECK_DAYS}
    cik_by_ticker = _cik_by_ticker(ticker_map)
    listed = young_candidates(ticker_map)
    for t in listed:
        if t not in cik_by_ticker:
            verdicts[t] = ("no_cik", today)
    candidates = [t for t in listed if t in cik_by_ticker and t not in settled]
    window_start = today - timedelta(days=screen_window_days(rules))
    recent = primary.fetch(candidates, window_start, today) if candidates else {}
    liquid = sorted(t for t, f in recent.items() if _current_and_liquid(f, rules, today))

    stored = {t: _first_bars(conn, t, primary.name) for t in liquid}
    stored = {t: bars for t, bars in stored.items() if bars != (None, None)}
    to_download = [t for t in liquid if t not in stored]
    full, splits = (fetch_frames_and_splits(primary, to_download, history_start, today)
                    if to_download else ({}, {}))

    young: list[tuple[str, date, pd.DataFrame | None]] = []
    for ticker in liquid:
        if ticker in stored:
            first = _primary_first_date(*stored[ticker])
            if first is None:
                continue  # fallback-built history: cannot date the listing until it is healed
            frame = None
        elif ticker in full:
            frame = _usable(full[ticker])
            if frame.empty:
                continue
            first = frame["date"].min().date()
        else:
            continue
        if first <= history_start + CLIPPED_MARGIN:
            verdicts[ticker] = ("old", today)
            continue  # may have been clipped by the start date: not young inside the window
        young.append((ticker, first, frame))

    # A newer listing under an old company's CIK is not a young company: an ETN under the issuing
    # bank, a further series of a commodity trust, a new class or note of an old issuer. Its price
    # times the company's share count is no market cap either.
    primary_of = _primary_ticker(ticker_map)
    secondary = [t for t, _, _ in young
                 if primary_of.get(t, t) != t and verdicts.get(primary_of[t], ("",))[0] == "old"]
    kept, rows = [], []
    for ticker, first, frame in young:
        if ticker in secondary:
            verdicts[ticker] = ("secondary", today)
            continue
        if frame is not None:
            replace_prices(conn, ticker, frame, primary.name, splits.get(ticker))
        rows.append((ticker, cik_by_ticker[ticker], first.isoformat(), today.isoformat()))
        verdicts[ticker] = ("young", today)
        kept.append(ticker)
    with conn:
        conn.executemany("DELETE FROM young_listings WHERE ticker = ?", [(t,) for t in secondary])
    upsert_rows(conn, "young_listings", ["ticker", "cik", "first_trade_date", "screened_on"], rows)
    _save_verdicts(conn, verdicts, today)
    return kept
