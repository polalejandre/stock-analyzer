"""Who can be scored (eligibility) and buy gates 1, 3 and 4 (spec 7.1, addendum 5.4).

Gate 2 (dislocation) is in features/dislocation.py; gate 5 (a plan) is in signals/plans.py.
Gate 3 is computed on whole panels, because the position plan's thesis stop needs it on every
day of a trade, not only on the signal day."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import Rules
from analyzer.features.quality import QualityFeatures
from analyzer.features.value import METRICS, ValueFeatures
from analyzer.panel.fundamentals import FundamentalPanels
from analyzer.signals.variants import FINANCIALS, REIT, SCORED, STANDARD

TRAP_RULES = ("revenue", "margin", "roe", "fcf", "leverage")
APPLIES = {
    "revenue": (STANDARD, FINANCIALS, REIT),
    "margin": (STANDARD, REIT),
    "roe": (FINANCIALS,),
    "fcf": (STANDARD,),
    "leverage": (STANDARD, REIT),
}


def pick(frame: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    ri = frame.index.get_indexer(pd.DatetimeIndex(rows["date"]))
    ci = frame.columns.get_indexer(rows["symbol"])
    ok = (ri >= 0) & (ci >= 0)
    arr = frame.to_numpy()
    out = np.full(len(rows), np.nan, dtype="float64") if arr.dtype != bool else np.zeros(len(rows), bool)
    out[ok] = arr[ri[ok], ci[ok]]
    return out


def _by_column(variants: pd.Series, columns, allowed) -> np.ndarray:
    return np.array([variants.get(c) in allowed for c in columns])


@dataclass(frozen=True)
class Eligibility:
    eligible: pd.DataFrame
    skipped: dict[str, int]


def eligibility(member: pd.DataFrame, variants: pd.Series, f: FundamentalPanels,
                market_cap: pd.DataFrame, rules: Rules) -> Eligibility:
    cols = member.columns
    scored = _by_column(variants, cols, SCORED)[None, :]
    first = f.first_filed.reindex(cols).to_numpy(dtype="datetime64[ns]")
    dates = member.index.to_numpy(dtype="datetime64[ns]")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        age = (dates[:, None] - first[None, :]) / np.timedelta64(1, "D")
    history = age >= rules.features.history_min_days            # NaT -> False
    fin = _by_column(variants, cols, (FINANCIALS,))[None, :]
    rev = f["revenue_ttm"].reindex(columns=cols).notna().to_numpy()
    op = f["operating_income_ttm"].reindex(columns=cols).notna().to_numpy()
    ni = f["net_income_ttm"].reindex(columns=cols).notna().to_numpy()
    required = rev & np.where(fin, ni, op)
    cap = market_cap.reindex(columns=cols).notna().to_numpy()

    m = member.to_numpy(dtype=bool)
    skipped = {}
    left = m.copy()
    for reason, ok in (("variant", np.broadcast_to(scored, m.shape)), ("history", history),
                       ("required", required), ("market_cap", cap)):
        skipped[reason] = int((left & ~ok).sum())
        left &= ok
    return Eligibility(pd.DataFrame(left, index=member.index, columns=cols), skipped)


@dataclass(frozen=True)
class TrapResult:
    fails: pd.DataFrame
    missing: pd.DataFrame
    unknown: dict[str, pd.DataFrame]


def value_trap(q: QualityFeatures, variants: pd.Series, rules: Rules) -> TrapResult:
    if rules.gates.guidance_tone_enabled:
        raise NotImplementedError("guidance tone is not built yet (addendum 2.2); "
                                  "set guidance_tone_enabled = false in rules.toml")
    cols = q.revenue_growth.columns
    g = rules.gates
    caps = np.array([rules.variants.reit.net_debt_ebitda_max if variants.get(c) == REIT
                     else rules.variants.standard_net_debt_ebitda_max for c in cols])
    fired = {
        "revenue": ((q.revenue_growth < 0) & q.revenue_decline_2q.astype(bool)).to_numpy(),
        "margin": (q.margin_change < -g.margin_drop).to_numpy(),
        "roe": (q.roe_change < -g.roe_drop).to_numpy(),
        "fcf": ((q.fcf < 0) & (q.fcf < q.fcf_prev)).to_numpy(),
        "leverage": (q.nd_ebitda.to_numpy() > caps[None, :]),
    }
    known = {
        "revenue": (q.revenue_growth.notna() & q.revenue_decline_known.astype(bool)).to_numpy(),
        "margin": q.margin_change.notna().to_numpy(),
        "roe": q.roe_change.notna().to_numpy(),
        "fcf": (q.fcf.notna() & q.fcf_prev.notna()).to_numpy(),
        "leverage": q.nd_ebitda.notna().to_numpy(),
    }
    fails = np.zeros(q.revenue_growth.shape, dtype=bool)
    missing = np.zeros(q.revenue_growth.shape, dtype=np.int8)
    unknown = {}
    for rule in TRAP_RULES:
        applies = _by_column(variants, cols, APPLIES[rule])[None, :]
        fails |= fired[rule] & known[rule] & applies
        unk = applies & ~known[rule]
        missing += unk.astype(np.int8)
        unknown[rule] = pd.DataFrame(unk, index=q.revenue_growth.index, columns=cols)
    idx = q.revenue_growth.index
    return TrapResult(pd.DataFrame(fails, index=idx, columns=cols),
                      pd.DataFrame(missing, index=idx, columns=cols), unknown)


def undervalued(rows: pd.DataFrame, vf: ValueFeatures, rules: Rules) -> pd.DataFrame:
    g = rules.gates
    present = [m for m in METRICS if m in vf.ratio]
    out = pd.DataFrame(index=rows.index)
    pcts, cheaper, avail = [], [], []
    for m in present:
        ratio, pct, peer = pick(vf.ratio[m], rows), pick(vf.pct[m], rows), pick(vf.peer[m], rows)
        ok = ~np.isnan(ratio) & ~np.isnan(pct)
        out[f"pct_{m}"] = np.where(ok, pct, np.nan)
        pcts.append(np.where(ok, pct, np.nan))
        cheaper.append(ok & ~np.isnan(peer) & (ratio < peer))
        avail.append(ok)
    for m in METRICS:
        if f"pct_{m}" not in out:
            out[f"pct_{m}"] = np.nan
    n = np.sum(avail, axis=0) if avail else np.zeros(len(rows), int)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(np.vstack(pcts), axis=0) if pcts else np.full(len(rows), np.nan)
    out["n_metrics"] = n
    out["median_pct"] = median
    out["cheaper"] = np.sum(cheaper, axis=0) if cheaper else 0
    out["gate1"] = ((out["n_metrics"] >= g.min_value_metrics)
                    & (out["median_pct"] <= g.max_value_percentile)
                    & (out["cheaper"] >= g.min_cheaper_metrics))
    return out
