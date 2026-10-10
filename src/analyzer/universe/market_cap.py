"""Point-in-time market cap: close x shares outstanding from the latest filing known that day.

Stored closes are split-adjusted to the basis of the latest full download, i.e. divided by every
split after their date. A share count reported as of date c is on the basis of that day, so it is
multiplied by every split after c. Then for a split s on or before d the factors cancel, and for
a split after d both sides carry it:

    true close on d x true shares on d = stored close on d x shares(c) x prod(splits after c)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from analyzer.data.edgar.facts_read import latest_shares_dated

SHARES_MAX_AGE_MONTHS = 15  # an older share count is no basis for a market cap


def months_before(d: date, months: int) -> date:
    """`d` moved back by whole calendar months; the day is clamped to the month's length."""
    total = d.year * 12 + d.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def split_factor_after(conn, symbol: str, after: date) -> float:
    """Product of the stored split ratios dated after `after` (1.0 when there are none)."""
    factor = 1.0
    for (ratio,) in conn.execute(
        "SELECT ratio FROM splits WHERE symbol = ? AND date > ? AND ratio > 0",
        (symbol, after.isoformat()),
    ):
        factor *= ratio
    return factor


def market_cap_on(conn, ticker: str, cik: int, d: date,
                  merges: Mapping[int, Sequence[int]] | None = None) -> float | None:
    """Market cap of `ticker` on `d`, or None without a close on or before `d`, without a share
    count known on `d`, or when that count is more than SHARES_MAX_AGE_MONTHS older than `d`."""
    row = conn.execute(
        "SELECT close FROM prices WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (ticker, d.isoformat()),
    ).fetchone()
    if row is None:
        return None
    known = latest_shares_dated(conn, cik, d, merges)
    if known is None:
        return None
    shares, as_of = known
    if as_of < months_before(d, SHARES_MAX_AGE_MONTHS):
        return None
    return row[0] * shares * split_factor_after(conn, ticker, as_of)
