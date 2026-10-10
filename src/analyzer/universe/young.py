"""Find liquid stocks that listed recently and are not in the S&P 500/400."""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from datetime import date, timedelta

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import HISTORY_START, replace_prices
from analyzer.data.prices import PriceProvider
from analyzer.store.db import upsert_rows
from analyzer.universe.members import MAX_BAR_AGE_DAYS

LISTED_EXCHANGES = {"NYSE", "Nasdaq"}
_NASDAQ_DERIVATIVE = re.compile(r"[A-Z]{4}[WUR]")          # warrants, units, rights
_SUFFIX_DERIVATIVE = re.compile(r"-(P[A-Z]?|WS?|WT|U|UN|R|RT)$")  # preferreds, warrants, units
SCREEN_WINDOW_DAYS = 45  # floor; longer liquidity windows widen it (see screen_window_days)
CLIPPED_MARGIN = timedelta(days=30)


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


def _stored_first_dates(conn, tickers: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ticker in tickers:
        (first,) = conn.execute(
            "SELECT MIN(date) FROM prices WHERE symbol = ?", (ticker,)).fetchone()
        if first is not None:
            out[ticker] = first
    return out


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
    downloaded or rewritten; their first stored bar dates the listing.
    """
    cik_by_ticker = _cik_by_ticker(ticker_map)
    candidates = [t for t in young_candidates(ticker_map) if t in cik_by_ticker]
    window_start = today - timedelta(days=screen_window_days(rules))
    recent = primary.fetch(candidates, window_start, today) if candidates else {}
    liquid = sorted(t for t, f in recent.items() if _current_and_liquid(f, rules, today))

    stored_first = _stored_first_dates(conn, liquid)
    to_download = [t for t in liquid if t not in stored_first]
    full = primary.fetch(to_download, history_start, today) if to_download else {}

    kept, rows = [], []
    for ticker in liquid:
        if ticker in stored_first:
            first = date.fromisoformat(stored_first[ticker])
            frame = None
        elif ticker in full:
            frame = _usable(full[ticker])
            if frame.empty:
                continue
            first = frame["date"].min().date()
        else:
            continue
        if first <= history_start + CLIPPED_MARGIN:
            continue  # may have been clipped by the start date: not young inside the window
        if frame is not None:
            replace_prices(conn, ticker, frame, primary.name)
        rows.append((ticker, cik_by_ticker[ticker], first.isoformat(), today.isoformat()))
        kept.append(ticker)
    upsert_rows(conn, "young_listings", ["ticker", "cik", "first_trade_date", "screened_on"], rows)
    return kept
