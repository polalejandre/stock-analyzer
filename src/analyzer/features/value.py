"""Value metrics (spec 5.1). Every metric is a price per unit, so lower is always cheaper:
P/E, EV/EBITDA, P/FCF (the inverse of FCF yield), P/S, P/B and P/FFO."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analyzer.config import FeatureRules
from analyzer.panel.fundamentals import FundamentalPanels

METRICS = ("pe", "ev_ebitda", "p_fcf", "ps", "pb", "p_ffo")
VARIANT_METRICS: dict[str, tuple[str, ...]] = {
    "standard": ("pe", "ev_ebitda", "p_fcf", "ps"),
    "financials": ("pe", "pb"),
    "reit": ("p_ffo", "ev_ebitda", "ps"),
}
LABELS = {"pe": "P/E", "ev_ebitda": "EV/EBITDA", "p_fcf": "FCF yield", "ps": "P/S",
          "pb": "P/B", "p_ffo": "P/FFO"}


def market_cap(close: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    return close * shares.astype("float64")


def net_debt(f: FundamentalPanels) -> pd.DataFrame:
    """Debt minus cash. A company that reports a balance sheet but no debt line has no debt."""
    debt = f["debt"].astype("float64").fillna(0.0).where(f["equity"].notna())
    return debt - f["cash"].astype("float64").fillna(0.0)


def ebitda(f: FundamentalPanels) -> pd.DataFrame:
    return f["operating_income_ttm"].astype("float64") + f["da_ttm"].astype("float64")


def _per(num: pd.DataFrame, den: pd.DataFrame) -> pd.DataFrame:
    den = den.astype("float64")
    return (num / den).where(den > 0)


def ratios(close: pd.DataFrame, f: FundamentalPanels,
           columns_for: Mapping[str, Sequence[str]] | None = None) -> dict[str, pd.DataFrame]:
    """All six metrics. With `columns_for`, each metric is computed only for its own symbols
    (metrics with no symbols are omitted); by default every metric covers every column."""
    cap_all = market_cap(close, f["shares"])
    nd_all = net_debt(f)
    ebitda_all = ebitda(f)
    fcf_all = f["ocf_ttm"].astype("float64") - f["capex_ttm"].astype("float64")
    ffo_all = f["net_income_ttm"].astype("float64") + f["da_ttm"].astype("float64")

    def part(frame: pd.DataFrame, cols) -> pd.DataFrame:
        return frame if cols is None else frame[cols]

    out: dict[str, pd.DataFrame] = {}
    for metric in METRICS:
        cols = None
        if columns_for is not None:
            cols = [c for c in columns_for.get(metric, ()) if c in close.columns]
            if not cols:
                continue
        cap = part(cap_all, cols)
        if metric == "pe":
            out[metric] = _per(cap, part(f["net_income_ttm"], cols))
        elif metric == "ev_ebitda":
            ev = cap + part(nd_all, cols)
            out[metric] = _per(ev, part(ebitda_all, cols)).where(ev > 0)
        elif metric == "p_fcf":
            out[metric] = _per(cap, part(fcf_all, cols))
        elif metric == "ps":
            out[metric] = _per(cap, part(f["revenue_ttm"], cols))
        elif metric == "pb":
            out[metric] = _per(cap, part(f["equity"], cols))
        else:
            out[metric] = _per(cap, part(ffo_all, cols))
    return out


def own_percentile(ratio: pd.DataFrame, window: int, min_obs: int) -> pd.DataFrame:
    """Percentile (0-100) of today's value among the symbol's own last `window` daily values."""
    return ratio.rolling(window, min_periods=min_obs).rank(pct=True) * 100


def _group_stats(values: np.ndarray, keys: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Per date and group: the median over the group's columns, and how many had a value."""
    med = np.full(values.shape, np.nan)
    cnt = np.zeros(values.shape, dtype=np.int32)
    codes = keys.to_numpy(dtype=object)
    for key in pd.unique(keys.dropna()):
        cols = np.flatnonzero(codes == key)
        block = values[:, cols]
        with warnings.catch_warnings():  # an all-NaN date gives NaN, which is what we want
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmedian(block, axis=1)
        med[:, cols] = m[:, None]
        cnt[:, cols] = (~np.isnan(block)).sum(axis=1)[:, None]
    return med, cnt


def peer_median(ratio: pd.DataFrame, eligible: pd.DataFrame, entities: pd.DataFrame,
                min_size: int) -> pd.DataFrame:
    """Median of the metric over the stock's industry peers in the universe that day, or over its
    sector when the industry has fewer than `min_size` members with a value (spec 5.1)."""
    values = ratio.where(eligible.reindex_like(ratio).fillna(False).astype(bool)).to_numpy(dtype="float64")
    ind = entities["industry"].reindex(ratio.columns)
    sec = entities["sector"].reindex(ratio.columns)
    ind_med, ind_cnt = _group_stats(values, ind)
    sec_med, _ = _group_stats(values, sec)
    out = np.where(ind_cnt >= min_size, ind_med, sec_med)
    return pd.DataFrame(out, index=ratio.index, columns=ratio.columns)


@dataclass(frozen=True)
class ValueFeatures:
    ratio: dict[str, pd.DataFrame]
    pct: dict[str, pd.DataFrame]
    p50: dict[str, pd.DataFrame]
    peer: dict[str, pd.DataFrame]
    market_cap: pd.DataFrame
    net_debt: pd.DataFrame
    ebitda: pd.DataFrame


def value_features(close: pd.DataFrame, f: FundamentalPanels, eligible: pd.DataFrame,
                   entities: pd.DataFrame, columns_for: Mapping[str, Sequence[str]],
                   rules: FeatureRules) -> ValueFeatures:
    """Ratios, own-history percentile and median, and peer median per metric.

    `columns_for[metric]` lists the symbols whose variant uses that metric (the caller builds it
    from all variants). Output frames for a metric hold only those columns, and the peer median
    is taken over eligible symbols among them. Metrics with no columns are omitted."""
    all_ratios = ratios(close, f, columns_for)
    r, pct, p50, peer = {}, {}, {}, {}
    for metric, x in all_ratios.items():
        r[metric] = x.astype("float32")
        pct[metric] = own_percentile(x, rules.percentile_window, rules.percentile_min_obs).astype("float32")
        p50[metric] = x.rolling(rules.percentile_window, min_periods=rules.percentile_min_obs
                                ).median().astype("float32")
        # Peer scope: x holds exactly columns_for[metric], so the peer median is taken over the
        # eligible symbols whose variant uses this metric (R16).
        peer[metric] = peer_median(x, eligible, entities, rules.peer_min_size).astype("float32")
    return ValueFeatures(r, pct, p50, peer, market_cap(close, f["shares"]), net_debt(f), ebitda(f))
