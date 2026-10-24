"""Trailing-window helpers on 2-D arrays (rows = dates, columns = symbols). Every result at row
t uses rows up to t only. Columns are processed in chunks to bound memory."""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

_CHUNK = 64


def since_max(values: np.ndarray, window: int) -> np.ndarray:
    n, m = values.shape
    out = np.full((n, m), np.nan)
    if n < window:
        return out
    for c0 in range(0, m, _CHUNK):
        v = values[:, c0:c0 + _CHUNK]
        win = sliding_window_view(v, window, axis=0)            # (n-window+1, k, window)
        gap = np.isnan(win).any(axis=2)
        rev = np.where(np.isnan(win), -np.inf, win)[..., ::-1]   # newest first: argmax = latest max
        since = np.argmax(rev, axis=2).astype(float)
        since[gap] = np.nan
        out[window - 1:, c0:c0 + _CHUNK] = since
    return out


def reduce_since(values: np.ndarray, since: np.ndarray, window: int, how: str) -> np.ndarray:
    """Reduce each column over its last `since[t] + 1` rows (since < window)."""
    if how not in ("min", "sum", "count"):
        raise ValueError(how)
    n, m = values.shape
    out = np.full((n, m), np.nan)
    if n == 0:
        return out
    pad = np.vstack([np.full((window - 1, m), np.nan), values])
    age = np.arange(window)[::-1]                               # window position -> rows back
    for c0 in range(0, m, _CHUNK):
        win = sliding_window_view(pad[:, c0:c0 + _CHUNK], window, axis=0)   # (n, k, window)
        s = since[:, c0:c0 + _CHUNK]
        with np.errstate(invalid="ignore"):
            keep = age[None, None, :] <= s[:, :, None]                  # NaN since -> all False
        vals = np.where(keep, win, np.nan)
        with np.errstate(all="ignore"):
            if how == "min":
                r = np.nanmin(np.where(np.isnan(vals), np.inf, vals), axis=2)
                r[np.isinf(r)] = np.nan
            elif how == "sum":
                r = np.nansum(vals, axis=2)
            else:
                r = (~np.isnan(vals)).sum(axis=2).astype(float)
        r[np.isnan(s)] = np.nan
        out[:, c0:c0 + _CHUNK] = r
    return out


def days_since(flag: pd.DataFrame) -> pd.DataFrame:
    pos = pd.DataFrame(np.arange(len(flag), dtype=float)[:, None].repeat(flag.shape[1], axis=1),
                       index=flag.index, columns=flag.columns)
    last = pos.where(flag.fillna(False).astype(bool)).ffill()
    return pos - last
