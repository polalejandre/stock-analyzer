"""Which stocks are in the universe on a given date."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from analyzer.config import UniverseRules
from analyzer.universe.market_cap import market_cap_on
from analyzer.universe.membership import INDEXES, members_on

BLANK_CHECK_SIC = "6770"
MAX_BAR_AGE_DAYS = 7  # a stock is only tradable on d if its newest bar is at most this old


@dataclass(frozen=True)
class Member:
    ticker: str
    cik: int | None
    source: str  # 'sp500' | 'sp400' | 'young'


def young_cutoff(d: date, rules: UniverseRules) -> date:
    """`d` minus the young-listing age in calendar years (Feb 29 maps to Feb 28)."""
    year = d.year - rules.young_max_years
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


def _recent_bars(conn, ticker: str, d: date, n: int) -> list[tuple[str, float, float]]:
    """The last `n` (date, close, volume) bars on or before `d`, newest first."""
    return conn.execute(
        "SELECT date, close, COALESCE(volume, 0) FROM prices WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (ticker, d.isoformat(), n),
    ).fetchall()


def _tradable_close(conn, ticker: str, d: date, rules: UniverseRules) -> float | None:
    """The latest close on or before `d`, if the ticker was tradable on `d`; otherwise None.

    Tradable means a full window of bars up to `d`, the newest at most MAX_BAR_AGE_DAYS old
    (so a delisted ticker or a gap in the data drops out), and an average dollar volume over
    the window of at least `min_dollar_volume`.
    """
    rows = _recent_bars(conn, ticker, d, rules.dollar_volume_days)
    if not rows or len(rows) < rules.dollar_volume_days:
        return None
    if (d - date.fromisoformat(rows[0][0])).days > MAX_BAR_AGE_DAYS:
        return None
    if sum(c * v for _, c, v in rows) / len(rows) < rules.min_dollar_volume:
        return None
    return rows[0][1]


def universe_on(conn, d: date, rules: UniverseRules,
                merges: Mapping[int, Sequence[int]] | None = None) -> list[Member]:
    cik_of = dict(conn.execute("SELECT ticker, cik FROM tickers").fetchall())
    index_members: dict[str, Member] = {}
    for index_name in INDEXES:
        for ticker in members_on(conn, index_name, d):
            index_members.setdefault(ticker, Member(ticker, cik_of.get(ticker), index_name))
    picked = [m for m in index_members.values()
              if _tradable_close(conn, m.ticker, d, rules) is not None]

    young = conn.execute(
        "SELECT y.ticker, y.cik FROM young_listings y LEFT JOIN companies c ON c.cik = y.cik "
        "WHERE y.first_trade_date > ? AND y.first_trade_date <= ? AND COALESCE(c.sic, '') <> ?",
        (young_cutoff(d, rules).isoformat(), d.isoformat(), BLANK_CHECK_SIC),
    ).fetchall()
    for ticker, cik in young:
        if ticker in index_members or cik is None:  # index membership wins
            continue
        if _tradable_close(conn, ticker, d, rules) is None:  # cheap checks before the shares lookup
            continue
        cap = market_cap_on(conn, ticker, cik, d, merges)
        if cap is not None and cap >= rules.young_min_market_cap:
            picked.append(Member(ticker, cik, "young"))

    return sorted(picked, key=lambda m: m.ticker)


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
