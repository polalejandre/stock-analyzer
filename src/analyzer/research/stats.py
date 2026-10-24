"""Summary statistics, month-block bootstrap intervals (spec 9.5) and the go/no-go (spec 9.6)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

from analyzer.config import Rules


def is_win(events: pd.DataFrame, rules: Rules) -> pd.Series:
    h = rules.research.go_horizon
    col = f"xs_spy_{h}" if rules.research.win_basis == "excess_spy" else f"ret_{h}"
    return events[col] > 0


def bootstrap_ci(values: np.ndarray, months: np.ndarray, stat: Callable[[np.ndarray], float],
                 samples: int, seed: int) -> tuple[float, float]:
    if len(values) == 0:
        return (float("nan"), float("nan"))
    uniq = np.unique(months)
    groups = [values[months == m] for m in uniq]
    rng = np.random.default_rng(seed)
    stats = np.empty(samples)
    for i in range(samples):
        idx = rng.integers(0, len(uniq), len(uniq))
        stats[i] = stat(np.concatenate([groups[j] for j in idx]))
    lo, hi = np.nanpercentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def _months(events: pd.DataFrame) -> np.ndarray:
    return pd.DatetimeIndex(events["date"]).to_period("M").astype(str).to_numpy()


def summarize(events: pd.DataFrame, rules: Rules) -> dict:
    h = rules.research.go_horizon
    e = events[events[f"xs_spy_{h}"].notna()]
    r = rules.research
    xs = e[f"xs_spy_{h}"].to_numpy(dtype=float)
    wins = is_win(e, rules).to_numpy(dtype=float)
    months = _months(e)
    nan = float("nan")
    has = len(e) > 0
    return {
        "n": int(len(e)),
        "median_ret": float(e[f"ret_{h}"].median()) if has else nan,
        "median_spy": float(e[f"spy_{h}"].median()) if has else nan,
        "median_xs_spy": float(np.median(xs)) if has else nan,
        "median_xs_ref": float(e[f"xs_ref_{h}"].median()) if has else nan,
        "win_rate": float(wins.mean()) if has else nan,
        "median_mae": float(e["mae"].median()) if has else nan,
        "ci_median_xs_spy": bootstrap_ci(xs, months, np.median, r.bootstrap_samples, r.bootstrap_seed),
        "ci_win_rate": bootstrap_ci(wins, months, np.mean, r.bootstrap_samples, r.bootstrap_seed),
    }


def quintiles(setups: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    h = rules.research.go_horizon
    e = setups[setups[f"xs_spy_{h}"].notna()]
    if len(e) < 5:
        return pd.DataFrame(columns=["quintile", "n", "median_xs_spy"])
    q = pd.qcut(e["score"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    g = e.groupby(q, observed=True)[f"xs_spy_{h}"]
    return pd.DataFrame({"quintile": [int(k) for k in g.groups], "n": g.size().to_numpy(),
                         "median_xs_spy": g.median().to_numpy()})


def breakdowns(events: pd.DataFrame, columns: Sequence[str], rules: Rules) -> pd.DataFrame:
    h = rules.research.go_horizon
    e = events[events[f"xs_spy_{h}"].notna()].assign(_win=lambda d: is_win(d, rules))
    parts = []
    for col in columns:
        if col not in e:
            continue
        g = e.groupby(e[col].astype(str))
        parts.append(pd.DataFrame({"column": col, "value": list(g.groups),
                                   "n": g.size().to_numpy(),
                                   "median_xs_spy": g[f"xs_spy_{h}"].median().to_numpy(),
                                   "win_rate": g["_win"].mean().to_numpy()}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["column", "value", "n", "median_xs_spy", "win_rate"])


def verdict(setups: dict, dislocated: dict, quintile_table: pd.DataFrame, rules: Rules) -> dict:
    r = rules.research
    q = (quintile_table.set_index("quintile")["median_xs_spy"] if len(quintile_table)
         else pd.Series(dtype=float))
    tests = {
        "n": bool(setups["n"] >= r.min_events),
        "median_xs_spy": bool(setups["median_xs_spy"] > 0),
        "win_rate": bool(setups["win_rate"] >= r.min_win_rate),
        "beats_dislocated": bool(setups["median_xs_spy"] > dislocated["median_xs_spy"]),
        "quintiles": bool(len(q) >= 2 and q.loc[q.index.max()] > q.loc[q.index.min()]),
    }
    return {"tests": tests, "passed": all(tests.values())}
