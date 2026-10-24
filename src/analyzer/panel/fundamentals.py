"""Daily fundamental panels: every company's step series, as-of joined onto trading dates.

Every dynamic field ages on the filing date of the value it shows. `first_filed`, `has_property`
and `has_us_gaap` are static per-company facts computed from all facts, not point-in-time: they
are used only as coarse classification (is it a REIT, a US-GAAP filer, how old is the filer),
which does not move once true, so they are not truncation-invariant by design.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.data.edgar.facts_read import load_concept
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.quarters import discrete_quarters
from analyzer.data.edgar.tags import CONCEPT_BY_NAME
from analyzer.panel.steps import (
    add_missing_periods,
    annual_steps,
    combine_ttm,
    fallback_ends,
    first_versions,
    instant_steps,
    latest_value_steps,
    quarter_steps,
    sum_periods,
    total_debt,
)

log = logging.getLogger(__name__)

DURATIONS = {  # panel prefix -> concept
    "revenue": "revenue", "operating_income": "operating_income", "net_income": "net_income",
    "eps": "eps_diluted", "da": "depreciation_amortization", "ocf": "operating_cash_flow",
    "capex": "capex",
}
NON_NEGATIVE = {"revenue", "capex", "depreciation_amortization"}
FLAGGED = ("revenue", "operating_income", "net_income", "ocf", "capex")  # feed annual/fallback
QUARTER_FIELDS = ("revenue_q0", "revenue_q0_ly", "revenue_q1", "revenue_q1_ly")
FIELDS = (*(f"{p}_ttm" for p in DURATIONS), *QUARTER_FIELDS,
          "equity", "equity_avg", "cash", "debt", "shares")
FLAGS = ("annual", "fallback", "diluted_shares")


@dataclass(frozen=True)
class FundamentalPanels:
    frames: dict[str, pd.DataFrame]
    first_filed: pd.Series
    has_property: pd.Series
    has_us_gaap: pd.Series

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.frames[name]

    def upto(self, end) -> FundamentalPanels:
        end = pd.Timestamp(end)
        return FundamentalPanels({k: v.loc[:end] for k, v in self.frames.items()},
                                 self.first_filed, self.has_property, self.has_us_gaap)


def merges_from_tickers(conn) -> dict[int, list[int]]:
    tm = pd.read_sql_query("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL", conn)
    return load_cik_merges(tm)


def load_splits(conn) -> dict[str, list[tuple[pd.Timestamp, float]]]:
    out: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    for symbol, d, ratio in conn.execute("SELECT symbol, date, ratio FROM splits WHERE ratio > 0"):
        out.setdefault(symbol, []).append((pd.Timestamp(d), float(ratio)))
    return out


def company_steps(conn, cik: int, merges: Mapping[int, Sequence[int]] | None = None
                  ) -> dict[str, pd.DataFrame]:
    def facts(concept: str) -> pd.DataFrame:
        return load_concept(conn, cik, concept, merges=merges, all_versions=True)

    out: dict[str, pd.DataFrame] = {}
    for prefix, concept in DURATIONS.items():
        f = facts(concept)
        ff = first_versions(f)
        q = discrete_quarters(ff)
        if concept in NON_NEGATIVE:
            q = q[~(q["derived"] & (q["value"] < 0))]
        annual_facts = ff
        if concept == "revenue":  # banks: NII + noninterest income where no revenue tag exists
            nii = first_versions(facts("net_interest_income"))
            non = first_versions(facts("noninterest_income"))
            q = add_missing_periods(q, sum_periods(discrete_quarters(nii), discrete_quarters(non)))
            bank_annual = sum_periods(nii.assign(days=0, derived=False),
                                      non.assign(days=0, derived=False))
            if len(bank_annual):
                bank_annual = bank_annual.assign(tag="bank", accession="bank", cik=cik)
                annual_facts = add_missing_periods(ff, bank_annual) if len(ff) else bank_annual
        fb = fallback_ends(f, CONCEPT_BY_NAME[concept].fallback_tags) if len(f) else frozenset()
        out[prefix] = combine_ttm(quarter_steps(q, fb), annual_steps(annual_facts, fb))
    out["equity"] = instant_steps(facts("stockholders_equity"))
    out["cash"] = instant_steps(facts("cash"))
    out["debt"] = instant_steps(total_debt(facts("long_term_debt"), facts("short_term_debt")),
                                keep_updates=True)
    out["shares_cover"] = instant_steps(facts("shares_outstanding"))
    out["shares_diluted"] = latest_value_steps(facts("diluted_shares"))
    return out


def _asof(steps: pd.DataFrame, dates: pd.DatetimeIndex, column: str) -> tuple[np.ndarray, np.ndarray]:
    """(values, index of the step used or -1) for each date: the last step filed on or before it."""
    if steps.empty:
        return np.full(len(dates), np.nan), np.full(len(dates), -1)
    filed = steps["filed"].to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(filed, dates.to_numpy(dtype="datetime64[ns]"), side="right") - 1
    values = steps[column].to_numpy(dtype="float64")[idx.clip(0)]
    values[idx < 0] = np.nan
    return values, idx


def _fresh(steps: pd.DataFrame, idx: np.ndarray, dates: pd.DatetimeIndex, column: str,
           max_age_days: int) -> np.ndarray:
    if steps.empty:
        return np.zeros(len(dates), dtype=bool)
    when = steps[column].to_numpy(dtype="datetime64[ns]")[idx.clip(0)]
    age = (dates.to_numpy(dtype="datetime64[ns]") - when) / np.timedelta64(1, "D")
    return (idx >= 0) & (age <= max_age_days)


def _split_adjusted(steps: pd.DataFrame, splits: list[tuple[pd.Timestamp, float]],
                    basis: str = "period_end") -> pd.DataFrame:
    """Share counts are on the basis of their own date; stored closes are on the basis of the
    latest download. Multiply each count by every split after its date (see market_cap.py)."""
    if steps.empty or not splits:
        return steps
    factor = [float(np.prod([r for d, r in splits if d > end] or [1.0])) for end in steps[basis]]
    return steps.assign(value=steps["value"] * np.array(factor))


def _company_columns(steps: dict[str, pd.DataFrame], dates: pd.DatetimeIndex,
                     splits: list[tuple[pd.Timestamp, float]], rules: FeatureRules
                     ) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    annual = np.zeros(len(dates), dtype=bool)
    fallback = np.zeros(len(dates), dtype=bool)
    for prefix in DURATIONS:
        s = steps[prefix]
        values, idx = _asof(s, dates, "ttm")
        fresh = _fresh(s, idx, dates, "value_filed", rules.ttm_max_age_days)
        cols[f"{prefix}_ttm"] = np.where(fresh, values, np.nan)
        if prefix == "revenue":
            for q in ("q0", "q0_ly", "q1", "q1_ly"):
                qv, qidx = _asof(s, dates, q)
                cols[f"revenue_{q}"] = np.where(_fresh(s, qidx, dates, "q_filed", rules.ttm_max_age_days),
                                                qv, np.nan)
        if prefix in FLAGGED and not s.empty:
            a, _ = _asof(s.assign(annual=s["annual"].astype(float)), dates, "annual")
            fb, _ = _asof(s.assign(fallback=s["fallback"].astype(float)), dates, "fallback")
            annual |= fresh & (a == 1)
            fallback |= fresh & (fb == 1)
    for name in ("equity", "cash", "debt"):
        s = steps[name]
        values, idx = _asof(s, dates, "value")
        cols[name] = np.where(_fresh(s, idx, dates, "value_filed", rules.ttm_max_age_days), values, np.nan)
        if name == "equity":
            avg, _ = _asof(s, dates, "avg5")
            cols["equity_avg"] = np.where(np.isnan(cols["equity"]), np.nan, avg)
    cover = _split_adjusted(steps["shares_cover"], splits)
    diluted = _split_adjusted(steps["shares_diluted"], splits, basis="filed")
    cv, cidx = _asof(cover, dates, "value")
    dv, didx = _asof(diluted, dates, "value")
    c_ok = _fresh(cover, cidx, dates, "period_end", rules.shares_max_age_days)
    d_ok = _fresh(diluted, didx, dates, "period_end", rules.shares_max_age_days)
    cols["shares"] = np.where(c_ok, cv, np.where(d_ok, dv, np.nan))
    cols["diluted_shares"] = ~c_ok & d_ok
    cols["annual"] = annual
    cols["fallback"] = fallback
    return cols


def load_fundamental_panels(conn, entities: pd.DataFrame, dates: pd.DatetimeIndex,
                            rules: FeatureRules) -> FundamentalPanels:
    merges = merges_from_tickers(conn)
    splits = load_splits(conn)
    property_ciks = {r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE concept = 'real_estate_property'")}
    gaap_ciks = {r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM facts WHERE concept IN ('revenue', 'net_income') "
        "AND tag LIKE 'us-gaap:%'")}
    first = dict(conn.execute(
        "SELECT cik, MIN(filed) FROM facts WHERE concept IN ('revenue', 'net_income', "
        "'net_interest_income') GROUP BY cik").fetchall())

    symbols = list(entities.index)
    failed: list[str] = []
    data: dict[str, dict[str, np.ndarray]] = {n: {} for n in (*FIELDS, *FLAGS)}
    first_filed, has_property, has_gaap = {}, {}, {}
    for n, symbol in enumerate(symbols, 1):
        cik = entities.at[symbol, "cik"]
        if pd.isna(cik):
            continue
        cik = int(cik)
        group = [cik, *merges.get(cik, [])]
        try:
            cols = _company_columns(company_steps(conn, cik, merges), dates,
                                    splits.get(symbol, []), rules)
        except Exception:  # one unreadable company must not stop the run; it is counted as missing
            log.exception("fundamentals for %s (CIK %d) failed", symbol, cik)
            failed.append(symbol)
            continue
        for name, values in cols.items():
            data[name][symbol] = values
        firsts = [first[c] for c in group if c in first]
        first_filed[symbol] = pd.Timestamp(min(firsts)) if firsts else pd.NaT
        has_property[symbol] = any(c in property_ciks for c in group)
        has_gaap[symbol] = any(c in gaap_ciks for c in group)
        if n % 200 == 0:
            log.info("fundamentals: %d/%d symbols", n, len(symbols))

    if failed:
        log.warning("fundamentals failed for %d of %d symbols, e.g. %s",
                    len(failed), len(symbols), ", ".join(failed[:5]))
    frames = {}
    for name in FIELDS:
        frames[name] = pd.DataFrame(data[name], index=dates).reindex(columns=symbols).astype("float32")
    for name in FLAGS:
        frames[name] = (pd.DataFrame(data[name], index=dates).reindex(columns=symbols)
                        .fillna(False).astype(bool))
    idx = pd.Index(symbols, name="symbol")
    return FundamentalPanels(
        frames=frames,
        first_filed=pd.Series(first_filed, dtype="datetime64[ns]").reindex(idx),
        has_property=pd.Series(has_property, dtype=bool).reindex(idx, fill_value=False),
        has_us_gaap=pd.Series(has_gaap, dtype=bool).reindex(idx, fill_value=False),
    )
