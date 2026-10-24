"""Synthetic panels and the truncation-invariance check (addendum 4.5)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analyzer.panel.prices import PricePanels


def random_walk(symbols, n: int = 400, seed: int = 0, start: str = "2018-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.02, size=(n, len(symbols)))
    closes = 100 * np.exp(np.cumsum(steps, axis=0))
    return pd.DataFrame(closes, index=pd.bdate_range(start, periods=n), columns=list(symbols))


def make_price_panels(closes: pd.DataFrame, volume: float = 1e6) -> PricePanels:
    """Open = high = low = close; adjusted close = close; constant volume."""
    vol = pd.DataFrame(volume, index=closes.index, columns=closes.columns)
    return PricePanels(open=closes, high=closes, low=closes, close=closes,
                       adj_close=closes, volume=vol)


def cut(obj, end):
    """`obj` as it would look with data only up to `end` (None: unchanged)."""
    if end is None:
        return obj
    if hasattr(obj, "upto"):
        return obj.upto(end)
    return obj.loc[:end]


def _frames(result) -> dict[str, pd.DataFrame]:
    """Every Series/DataFrame in `result` (a frame, a dict, or a dataclass), by name."""
    if isinstance(result, (pd.Series, pd.DataFrame)):
        result = {"value": result}
    elif not isinstance(result, dict):
        result = vars(result)
    return {k: (v.to_frame() if isinstance(v, pd.Series) else v)
            for k, v in result.items() if isinstance(v, (pd.Series, pd.DataFrame))}


def assert_truncation_invariant(compute, cut_dates) -> None:
    """`compute(end)` must build its result from inputs cut at `end` (None = full data): it must
    apply `cut` to its raw inputs before any calculation. Slicing a full-data result afterwards
    defeats the check. The row at each cut date must be identical whether or not later data
    exists."""
    cut_dates = list(cut_dates)
    assert cut_dates, "cut_dates must not be empty"
    full = _frames(compute(None))
    assert full, "compute returned no frames"
    for d in cut_dates:
        d = pd.Timestamp(d)
        part = _frames(compute(d))
        for name, frame in full.items():
            pd.testing.assert_series_equal(
                frame.loc[d], part[name].loc[d], check_names=False, check_dtype=False,
                check_exact=False, rtol=1e-9, atol=1e-12,
                obj=f"{name} at {d.date()}")
