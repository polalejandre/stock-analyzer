"""Which stocks are in the universe on a given date."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from analyzer.config import UniverseRules
from analyzer.data.edgar.facts_read import latest_shares
from analyzer.universe.membership import INDEXES, members_on

BLANK_CHECK_SIC = "6770"


@dataclass(frozen=True)
class Member:
    ticker: str
    cik: int | None
    source: str  # 'sp500' | 'sp400' | 'young'


def young_cutoff(d: date, rules: UniverseRules) -> date:
    return d - timedelta(days=round(365.25 * rules.young_max_years))


def _recent_bars(conn, ticker: str, d: date, n: int) -> list[tuple[float, float]]:
    return conn.execute(
        "SELECT close, COALESCE(volume, 0) FROM prices WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (ticker, d.isoformat(), n),
    ).fetchall()


def _liquid(conn, ticker: str, d: date, rules: UniverseRules) -> bool:
    rows = _recent_bars(conn, ticker, d, rules.dollar_volume_days)
    if len(rows) < rules.dollar_volume_days:
        return False
    return sum(c * v for c, v in rows) / len(rows) >= rules.min_dollar_volume


def universe_on(conn, d: date, rules: UniverseRules,
                merges: Mapping[int, Sequence[int]] | None = None) -> list[Member]:
    cik_of = dict(conn.execute("SELECT ticker, cik FROM tickers").fetchall())
    picked: dict[str, Member] = {}
    for index_name in INDEXES:
        for ticker in members_on(conn, index_name, d):
            picked.setdefault(ticker, Member(ticker, cik_of.get(ticker), index_name))

    young = conn.execute(
        "SELECT y.ticker, y.cik FROM young_listings y LEFT JOIN companies c ON c.cik = y.cik "
        "WHERE y.first_trade_date > ? AND y.first_trade_date <= ? AND COALESCE(c.sic, '') <> ?",
        (young_cutoff(d, rules).isoformat(), d.isoformat(), BLANK_CHECK_SIC),
    ).fetchall()
    for ticker, cik in young:
        if ticker in picked or cik is None:
            continue
        bars = _recent_bars(conn, ticker, d, 1)
        shares = latest_shares(conn, cik, d, merges)
        if bars and shares is not None and bars[0][0] * shares >= rules.young_min_market_cap:
            picked[ticker] = Member(ticker, cik, "young")

    return sorted((m for m in picked.values() if _liquid(conn, m.ticker, d, rules)),
                  key=lambda m: m.ticker)


def price_symbols(conn, today: date, rules: UniverseRules) -> tuple[list[str], list[str]]:
    """Symbols to refresh daily (active) and past members to fetch once (historical)."""
    active: set[str] = set()
    for index_name in INDEXES:
        active |= members_on(conn, index_name, today)
    active |= {r[0] for r in conn.execute(
        "SELECT ticker FROM young_listings WHERE first_trade_date > ?",
        (young_cutoff(today, rules).isoformat(),))}
    ever = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM index_membership")}
    ever |= {r[0] for r in conn.execute("SELECT ticker FROM young_listings")}
    return sorted(active), sorted(ever - active)
