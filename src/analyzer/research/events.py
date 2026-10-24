"""Events: the first qualifying day of a ticker in a group, then a cooldown (addendum 6.2), and
the period each event belongs to (addendum 6.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analyzer.config import Rules

GROUP_SETUP = "setup"
GROUP_DISLOCATED = "dislocated"
GROUP_DISLOCATED_ALL = "dislocated_all"


def cooldown_mask(rows: pd.DataFrame, days: int) -> np.ndarray:
    keep = np.zeros(len(rows), dtype=bool)
    symbols = rows["symbol"].to_numpy()
    pos = rows["pos"].to_numpy()
    last: dict[str, int] = {}
    for i in np.lexsort((pos, symbols)):
        s, p = symbols[i], pos[i]
        if s not in last or p - last[s] >= days:
            keep[i] = True
            last[s] = p
    return keep


def select_events(evaluation, days: int) -> pd.DataFrame:
    rows = evaluation.rows
    setups = rows[rows["setup"]]
    parts = [
        rows[cooldown_mask(rows, days)].assign(group=GROUP_DISLOCATED),
        setups[cooldown_mask(setups, days)].assign(group=GROUP_SETUP),
        evaluation.dislocated_all[cooldown_mask(evaluation.dislocated_all, days)]
        .assign(group=GROUP_DISLOCATED_ALL),
    ]
    return pd.concat(parts, ignore_index=True)


def assign_period(events: pd.DataFrame, dates: pd.DatetimeIndex, rules: Rules) -> pd.Series:
    r = rules.research
    start = dates.searchsorted(pd.Timestamp(r.start))
    oos = dates.searchsorted(pd.Timestamp(r.oos_start))
    last = len(dates) - 1
    pos = events["pos"].to_numpy()
    before_oos = (pos >= start) & (pos < oos)
    in_sample = before_oos & (pos + r.purge_horizon < min(oos, last + 1))
    oos_full = (pos >= oos) & (pos + r.go_horizon <= last)
    labels = np.select(
        [in_sample, before_oos, oos_full, pos >= oos],
        ["in_sample", "purged", "oos", "oos_incomplete"], default="before_start")
    return pd.Series(labels, index=events.index)
