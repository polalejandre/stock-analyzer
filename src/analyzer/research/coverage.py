"""How much of the point-in-time universe research actually sees (spec 9.3, addendum 6.6)."""

from __future__ import annotations

from analyzer.panel.universe import symbol_map


def _members_by_year(conn, first_year: int, last_year: int) -> list[dict]:
    priced = symbol_map(conn)
    rows = conn.execute("SELECT ticker, start_date, COALESCE(end_date, '9999-12-31') "
                        "FROM index_membership").fetchall()
    out = []
    for year in range(first_year, last_year + 1):
        lo, hi = f"{year}-01-01", f"{year}-12-31"
        members = {t for t, s, e in rows if s <= hi and e > lo}
        with_prices = sum(1 for t in members if t in priced)
        out.append({"year": year, "members": len(members), "with_prices": with_prices,
                    "share": with_prices / len(members) if members else float("nan")})
    return out


def coverage(conn, panels, evaluation) -> dict:
    dates = panels.dates
    f = panels.fundamentals
    eligible = evaluation.eligible
    return {
        "members_by_year": _members_by_year(conn, dates[0].year, dates[-1].year),
        "aliases": dict(conn.execute(
            "SELECT source, COUNT(*) FROM ticker_aliases GROUP BY source").fetchall()),
        "excluded": dict(conn.execute(
            "SELECT fate, COUNT(*) FROM member_fates GROUP BY fate").fetchall()),
        "identity_unverified": int((~panels.entities["identity_verified"].astype(bool)).sum()),
        "variants": {k: int(v) for k, v in evaluation.variants.value_counts().items()},
        "skipped_ticker_days": evaluation.skipped,
        "diluted_share_days": int((evaluation.member & f["diluted_shares"]).to_numpy().sum()),
        "annual_ttm_days": int((eligible & f["annual"]).to_numpy().sum()),
        "fallback_days": int((eligible & f["fallback"]).to_numpy().sum()),
        # ticker-days whose reference ETF that day was a fallback (the industry ETF, or the
        # sector ETF, had no 63-day return yet or no prices): universe members and eligible rows
        "ref_fallback_days": {
            "member": int((evaluation.member & evaluation.ref_fallback).to_numpy().sum()),
            "eligible": int((eligible & evaluation.ref_fallback).to_numpy().sum()),
        },
    }
