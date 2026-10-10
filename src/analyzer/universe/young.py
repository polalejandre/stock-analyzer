"""Find liquid stocks that listed recently and are not in the S&P 500/400."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import HISTORY_START, replace_prices
from analyzer.data.prices import PriceProvider
from analyzer.store.db import upsert_rows

LISTED_EXCHANGES = {"NYSE", "Nasdaq"}
_NASDAQ_DERIVATIVE = re.compile(r"[A-Z]{4}[WUR]")          # warrants, units, rights
_SUFFIX_DERIVATIVE = re.compile(r"-(P[A-Z]?|WS?|WT|U|UN|R|RT)$")  # preferreds, warrants, units
SCREEN_WINDOW_DAYS = 45
CLIPPED_MARGIN = timedelta(days=30)


def young_candidates(ticker_map: pd.DataFrame, exclude: set[str]) -> list[str]:
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


def screen_young_listings(
    conn,
    primary: PriceProvider,
    ticker_map: pd.DataFrame,
    index_tickers: set[str],
    rules: UniverseRules,
    today: date,
    history_start: date = HISTORY_START,
) -> list[str]:
    cik_by_ticker = _cik_by_ticker(ticker_map)
    candidates = [t for t in young_candidates(ticker_map, index_tickers) if t in cik_by_ticker]
    window_start = today - timedelta(days=SCREEN_WINDOW_DAYS)
    recent = primary.fetch(candidates, window_start, today) if candidates else {}
    liquid = [
        t for t, f in recent.items()
        if len(_usable(f)) >= rules.dollar_volume_days
        and average_dollar_volume(f, rules.dollar_volume_days) >= rules.min_dollar_volume
    ]
    full = primary.fetch(liquid, history_start, today) if liquid else {}
    kept, rows = [], []
    for ticker in sorted(full):
        frame = _usable(full[ticker])
        if frame.empty or ticker not in cik_by_ticker:
            continue
        first = frame["date"].min().date()
        if first <= history_start + CLIPPED_MARGIN:
            continue
        replace_prices(conn, ticker, frame, primary.name)
        rows.append((ticker, cik_by_ticker[ticker], first.isoformat(), today.isoformat()))
        kept.append(ticker)
    upsert_rows(conn, "young_listings", ["ticker", "cik", "first_trade_date", "screened_on"], rows)
    return kept
