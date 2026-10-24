"""Score (0-100, ranking only) and tiers (spec 7.3). Weights are fixed, never fitted."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from analyzer.config import Rules
from analyzer.features.flow import COLD, HOT
from analyzer.features.quality import balance_score
from analyzer.signals.variants import FINANCIALS, REIT

QUALITY_PARTS = {  # guidance tone is left out of the first run (addendum 2.3)
    "standard": ("growth_score", "margin_score", "fcf_score", "balance"),
    FINANCIALS: ("growth_score", "roe_score"),
    REIT: ("growth_score", "margin_score", "balance"),
}


def _quality(rows: pd.DataFrame, rules: Rules) -> np.ndarray:
    nd = pd.DataFrame({"x": rows["nd_ebitda"].to_numpy(dtype="float64")})
    balance = np.where(rows["variant"] == REIT,
                       balance_score(nd, rules.variants.reit.net_debt_ebitda_max)["x"],
                       balance_score(nd, rules.variants.standard_net_debt_ebitda_max)["x"])
    parts = {"growth_score": rows["growth_score"], "margin_score": rows["margin_score"],
             "roe_score": rows["roe_score"], "fcf_score": rows["fcf_score"], "balance": balance}
    out = np.full(len(rows), np.nan)
    for i, variant in enumerate(rows["variant"]):
        names = QUALITY_PARTS.get(variant, ())
        vals = [float(np.asarray(parts[n])[i]) for n in names]
        vals = [v for v in vals if not np.isnan(v)]
        out[i] = float(np.mean(vals)) if vals else 0.0
    return out


def score(rows: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    s, g = rules.score, rules.gates
    out = pd.DataFrame(index=rows.index)
    depth = ((g.max_value_percentile - rows["median_pct"]) / g.max_value_percentile).clip(0, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        share = (rows["cheaper"] / rows["n_metrics"]).fillna(0)
    out["c_value"] = 0.5 * depth.fillna(0) + 0.5 * share
    out["c_quality"] = _quality(rows, rules)
    out["c_dislocation"] = np.where(rows["correction"].astype(bool), s.dislocation_correction_drift,
                                    s.dislocation_idiosyncratic_drift)
    industry = rows["industry"].to_numpy(dtype="float64")
    industry_score = np.where(industry == HOT, s.flow_hot,
                              np.where(industry == COLD, s.flow_cold, s.flow_neutral))
    out["c_flow"] = 0.5 * industry_score + 0.5 * rows["accumulation"].fillna(0).to_numpy()
    out["c_technical"] = (rows["divergence"].astype(float) + rows["reclaimed"].astype(float)) / 2
    total = (s.weight_value * out["c_value"] + s.weight_quality * out["c_quality"]
             + s.weight_dislocation * out["c_dislocation"] + s.weight_flow * out["c_flow"]
             + s.weight_technical * out["c_technical"]
             + s.insider_bonus * rows["insider_cluster"].astype(float))
    out["score"] = total.clip(upper=100)
    out["tier"] = np.where(out["score"] >= s.tier_a, "A", np.where(out["score"] >= s.tier_b, "B", "C"))
    return out
