"""Predecessor CIKs whose history belongs to a current company."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path(__file__).with_name("cik_merges.toml")


def load_cik_merges(ticker_map: pd.DataFrame, path: Path = DEFAULT_PATH) -> dict[int, list[int]]:
    config = tomllib.loads(Path(path).read_text())
    cik_by_ticker = dict(zip(ticker_map["ticker"], ticker_map["cik"].astype(int), strict=True))
    out: dict[int, list[int]] = {}
    for entry in config.get("merge", []):
        current = cik_by_ticker.get(entry["ticker"])
        if current is None:
            continue
        preds = [int(p) for p in entry["predecessor_ciks"] if int(p) != current]
        if preds:
            out[current] = preds
    return out
