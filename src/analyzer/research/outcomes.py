"""What happened after each event: forward returns from the next open (addendum 6.2) and
plan outcomes on daily closes (addendum 6.4)."""

from __future__ import annotations

import warnings
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


def _last_valid(arr: np.ndarray) -> np.ndarray:
    """Per cell, the row of the latest non-NaN value at or before it (-1 if none)."""
    rows = np.where(np.isnan(arr), -1, np.arange(arr.shape[0])[:, None])
    return np.maximum.accumulate(rows, axis=0)


def forward_returns(events: pd.DataFrame, stocks: PricePanels, etfs: PricePanels,
                    ref_of: Mapping[str, str], horizons: Sequence[int], mae_horizon: int) -> pd.DataFrame:
    """Forward returns from the next open. A stock that stops trading mid-window exits at its last
    valid close and then holds cash (so delistings stay in the statistics); `delisted_h` marks a
    stock that is gone for good, as opposed to a gap. The benchmarks always run to `pos + h`.
    The reference ETF is the event's `ref_etf` column when present (that day's reference)."""
    assert stocks.dates.equals(etfs.dates), "stock and ETF panels must share one calendar"
    n = len(stocks.dates)
    pos = events["pos"].to_numpy(dtype=int)
    entry_row = pos + 1
    ci = stocks.close.columns.get_indexer(events["symbol"])
    ci_ok = np.maximum(ci, 0)
    spy_ci = np.full(len(events), etfs.close.columns.get_loc("SPY"))
    # the event day's reference ETF (with the per-day fallback, from evaluate) when the events
    # carry it; otherwise the static mapping
    refs = (events["ref_etf"].where(events["ref_etf"].notna(), None).tolist()
            if "ref_etf" in events else [ref_of.get(s, "SPY") for s in events["symbol"]])
    ref_ci = etfs.close.columns.get_indexer(refs)
    s_open, s_close = stocks.adj_open.to_numpy(), stocks.adj_close.to_numpy()
    e_open, e_close = etfs.adj_open.to_numpy(), etfs.adj_close.to_numpy()
    last_valid = _last_valid(s_close)
    entry = _at(s_open, entry_row, ci)
    spy_entry, ref_entry = _at(e_open, entry_row, spy_ci), _at(e_open, entry_row, ref_ci)
    out = pd.DataFrame(index=events.index)
    out["entry_price"] = entry
    for h in horizons:
        exit_row = pos + h
        complete = (exit_row < n) & (ci >= 0)
        lv = np.where(complete, last_valid[np.clip(exit_row, 0, n - 1), ci_ok], -1)
        usable = complete & (lv >= entry_row)
        exit_px = np.where(usable, s_close[np.maximum(lv, 0), ci_ok], np.nan)
        ret = exit_px / entry - 1
        spy = _at(e_close, exit_row, spy_ci) / spy_entry - 1
        ref = _at(e_close, exit_row, ref_ci) / ref_entry - 1
        out[f"ret_{h}"], out[f"spy_{h}"] = ret, spy
        out[f"xs_spy_{h}"], out[f"xs_ref_{h}"] = ret - spy, ret - ref
        out[f"delisted_{h}"] = usable & (last_valid[n - 1, ci_ok] == lv) & (lv < n - 1)
    adj_low = (stocks.low * stocks.adj_close / stocks.close).to_numpy()
    mae = np.full(len(events), np.nan)
    for i, (p, c) in enumerate(zip(pos, ci, strict=True)):
        last = p + mae_horizon
        if c < 0 or p + 1 >= n or last >= n:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
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
    valid = np.flatnonzero(~np.isnan(close))
    lv = int(valid[-1]) if len(valid) else -1
    if lv < e:
        return "no_data", np.nan, np.nan, 0
    for d in range(e, min(last, n - 1) + 1):
        c = close[d]
        if np.isnan(c):
            continue
        if c <= stop:
            return "stop", c, (c - entry) / risk, d - e + 1
        if c >= target:
            return "target", c, (c - entry) / risk, d - e + 1
        if plan == "position" and trap[d] and d < last and d + 1 < n:
            x = open_[d + 1]
            if np.isnan(x):
                return "thesis", c, (c - entry) / risk, d - e + 1
            return "thesis", x, (x - entry) / risk, d - e + 2
    if lv < min(last, n - 1):
        return "delisted", close[lv], (close[lv] - entry) / risk, lv - e + 1
    if last <= n - 1:
        k = int(valid[valid <= last][-1])
        return "time", close[k], (close[k] - entry) / risk, time_stop
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
