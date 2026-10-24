"""Swing and position plans (spec 8.1) and gate 5 (a plan passes the reward/risk gate)."""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import numpy as np
import pandas as pd

from analyzer.config import GateRules, PlanRules
from analyzer.features.value import ValueFeatures
from analyzer.signals.gates import pick


def valuation_target(rows: pd.DataFrame, vf: ValueFeatures,
                     metrics_by_row: Sequence[Sequence[str]]) -> np.ndarray:
    close = rows["close"].to_numpy(dtype="float64")
    shares = pick(vf.market_cap, rows) / close
    ebitda, nd = pick(vf.ebitda, rows), pick(vf.net_debt, rows)
    targets = {}
    for m in vf.ratio:
        ratio, p50 = pick(vf.ratio[m], rows), pick(vf.p50[m], rows)
        with np.errstate(all="ignore"):
            if m == "ev_ebitda":
                t = (p50 * ebitda - nd) / shares
                t = np.where((ebitda > 0) & (shares > 0), t, np.nan)
            else:
                t = close * p50 / ratio
                t = np.where((ratio > 0) & (p50 > 0), t, np.nan)
            targets[m] = t
    out = np.full(len(rows), np.nan)
    for i, metrics in enumerate(metrics_by_row):
        vals = [targets[m][i] for m in metrics if m in targets and np.isfinite(targets[m][i])]
        if vals:
            out[i] = float(np.median(vals))
    return out


def _closest_above(entry: np.ndarray, *levels: np.ndarray) -> np.ndarray:
    stacked = np.vstack(levels)
    above = np.where(stacked > entry[None, :], stacked, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmin(above, axis=0)


def make_plans(rows: pd.DataFrame, plans: PlanRules, gates: GateRules) -> pd.DataFrame:
    entry = rows["close"].to_numpy(dtype="float64")
    atr = rows["atr"].to_numpy(dtype="float64")
    low = rows["window_low"].to_numpy(dtype="float64")
    high = rows["high"].to_numpy(dtype="float64")
    out = pd.DataFrame(index=rows.index)
    out["entry"] = entry
    out["zone_low"] = entry - plans.entry_zone_atr * atr
    out["zone_high"] = entry + plans.entry_zone_atr * atr

    retrace = low + plans.swing_retrace * (high - low)
    out["swing_stop"] = low - plans.swing_stop_atr * atr
    out["swing_target"] = _closest_above(entry, retrace, rows["ma"].to_numpy(dtype="float64"))
    out["position_stop"] = low - plans.position_stop_atr * atr
    out["position_target"] = _closest_above(entry, high, rows["val_target"].to_numpy(dtype="float64"))
    for plan in ("swing", "position"):
        with np.errstate(all="ignore"):
            risk = entry - out[f"{plan}_stop"]
            rr = ((out[f"{plan}_target"] - entry) / risk).where(risk > 0)
        out[f"{plan}_rr"] = rr
        out[f"{plan}_ok"] = (rr >= gates.min_reward_risk).fillna(False).astype(bool)
    out["gate5"] = out["swing_ok"] | out["position_ok"]
    return out
