"""What happened after each event: forward returns from the next open (addendum 6.2) and
plan outcomes on daily closes (addendum 6.4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from analyzer.config import PlanRules
from analyzer.panel.prices import PricePanels


def _at(arr: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0)
    out = np.full(len(rows), np.nan)
    out[ok] = arr[rows[ok], cols[ok]]
    return out


def forward_returns(events: pd.DataFrame, stocks: PricePanels, etfs: PricePanels,
                    ref_of: Mapping[str, str], horizons: Sequence[int], mae_horizon: int) -> pd.DataFrame:
    n = len(stocks.dates)
    pos = events["pos"].to_numpy(dtype=int)
    entry_row = pos + 1
    ci = stocks.close.columns.get_indexer(events["symbol"])
    spy_ci = np.full(len(events), etfs.close.columns.get_loc("SPY"))
    ref_ci = etfs.close.columns.get_indexer([ref_of.get(s, "SPY") for s in events["symbol"]])
    s_open, s_close = stocks.adj_open.to_numpy(), stocks.adj_close.to_numpy()
    e_open, e_close = etfs.adj_open.to_numpy(), etfs.adj_close.to_numpy()
    entry = _at(s_open, entry_row, ci)
    spy_entry, ref_entry = _at(e_open, entry_row, spy_ci), _at(e_open, entry_row, ref_ci)
    out = pd.DataFrame(index=events.index)
    out["entry_price"] = entry
    for h in horizons:
        exit_row = pos + h
        ret = _at(s_close, exit_row, ci) / entry - 1
        spy = _at(e_close, exit_row, spy_ci) / spy_entry - 1
        ref = _at(e_close, exit_row, ref_ci) / ref_entry - 1
        out[f"ret_{h}"], out[f"spy_{h}"] = ret, spy
        out[f"xs_spy_{h}"], out[f"xs_ref_{h}"] = ret - spy, ret - ref
    adj_low = (stocks.low * stocks.adj_close / stocks.close).to_numpy()
    mae = np.full(len(events), np.nan)
    for i, (p, c) in enumerate(zip(pos, ci, strict=True)):
        last = p + mae_horizon
        if c < 0 or p + 1 >= n or last >= n:
            continue
        mae[i] = np.nanmin(adj_low[p + 1:last + 1, c]) / entry[i] - 1
    out["mae"] = mae
    return out


def simulate_plan(open_: np.ndarray, close: np.ndarray, trap: np.ndarray, pos: int,
                  plan_row: Mapping, plan: str, time_stop: int) -> tuple[str, float, float, int]:
    n = len(close)
    e = pos + 1
    if e >= n or np.isnan(open_[e]):
        return "no_data", np.nan, np.nan, 0
    entry = open_[e]
    if not plan_row["zone_low"] <= entry <= plan_row["zone_high"]:
        return "no_fill", np.nan, np.nan, 0
    stop, target = plan_row[f"{plan}_stop"], plan_row[f"{plan}_target"]
    risk = entry - stop
    last = e + time_stop - 1
    for d in range(e, min(last, n - 1) + 1):
        c = close[d]
        if np.isnan(c):
            continue
        if c <= stop:
            return "stop", c, (c - entry) / risk, d - e + 1
        if c >= target:
            return "target", c, (c - entry) / risk, d - e + 1
        if plan == "position" and trap[d] and d + 1 < n:
            x = open_[d + 1]
            return "thesis", x, (x - entry) / risk, d - e + 2
    if last <= n - 1:
        return "time", close[last], (close[last] - entry) / risk, time_stop
    return "open", close[n - 1], (close[n - 1] - entry) / risk, n - e


def simulate_plans(events: pd.DataFrame, stocks: PricePanels, trap_fails: pd.DataFrame,
                   plans: PlanRules) -> pd.DataFrame:
    out = pd.DataFrame(index=events.index)
    opens, closes = stocks.open, stocks.close
    trap = trap_fails.reindex(index=stocks.dates, columns=closes.columns).fillna(False)
    results = {f"{p}_{k}": [] for p in ("swing", "position") for k in ("outcome", "r", "days")}
    for row in events.to_dict("records"):
        s = row["symbol"]
        o, c, t = opens[s].to_numpy(), closes[s].to_numpy(), trap[s].to_numpy(dtype=bool)
        for plan, horizon in (("swing", plans.swing_time_stop), ("position", plans.position_time_stop)):
            if np.isnan(row[f"{plan}_target"]):
                outcome, r, days = "no_plan", np.nan, 0
            else:
                outcome, _price, r, days = simulate_plan(o, c, t, int(row["pos"]), row, plan, horizon)
            results[f"{plan}_outcome"].append(outcome)
            results[f"{plan}_r"].append(r)
            results[f"{plan}_days"].append(days)
    for k, v in results.items():
        out[k] = v
    return out
