"""Reason strings: one per feature that fires (spec 7.6)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from analyzer.config import Rules
from analyzer.features.flow import COLD, HOT
from analyzer.features.value import LABELS, VARIANT_METRICS


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def reasons(row: Mapping, rules: Rules) -> list[str]:
    out = []
    for m in VARIANT_METRICS.get(row["variant"], ()):
        pct = _num(row.get(f"pct_{m}"))
        if not np.isnan(pct):
            out.append(f"{LABELS[m]} at {pct:.0f}th percentile of its 5-year range")
    out.append(f"Cheaper than peers on {int(row['cheaper'])} of {int(row['n_metrics'])} metrics")
    out.append(f"Down {_num(row['drawdown']):.0%} from its 63-day high, "
               f"{_num(row['gap']):+.0%} vs {row['ref_etf']}")
    out.append("Market correction, drift" if row["correction"] else "Idiosyncratic drop, drift")
    industry = _num(row["industry"])
    if industry == HOT:
        out.append(f"Industry hot ({row['ref_etf']})")
    elif industry == COLD:
        out.append(f"Industry cold ({row['ref_etf']})")
    if row["divergence"]:
        out.append("Bullish RSI divergence")
    if row["reclaimed"]:
        out.append("Reclaimed support")
    if row["insider_cluster"]:
        if row.get("executive_buy"):
            out.append("Insider buying: CEO/CFO purchase")
        else:
            out.append(f"Insider buying: {int(row.get('buyers', 0))} insiders")
    if row.get("missing_rules"):
        out.append(f"Value-trap rule skipped (no data): {row['missing_rules']}")
    if row.get("annual"):
        out.append("TTM from the annual report (quarters missing)")
    if row.get("fallback"):
        out.append("Uses fallback tags (hindsight-chosen; addendum 5.2)")
    return out
