"""Opt-in truncation invariance on the real database (addendum 4.5): uv run pytest -m realdb -v

`evaluate()` on a sample of real symbols, from all data up to END and from data cut at each CUT,
must give identical rows up to the cut. The database is opened read-only; each run builds the
needed rows into in-memory copies."""

from __future__ import annotations

import random
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analyzer.config import load_rules
from analyzer.panel.fundamentals import merges_from_tickers
from analyzer.panel.universe import symbol_map
from analyzer.signals.evaluate import evaluate, load_panels
from analyzer.universe.industry import ALL_REFERENCE_ETFS

pytestmark = pytest.mark.realdb

DB = Path(__file__).resolve().parents[2] / "data" / "analyzer.db"
SRC = f"file:{DB}?mode=ro"
END = date(2021, 12, 31)            # in-sample only: never read out-of-sample prices
CUTS = [date(2016, 8, 15), date(2018, 6, 29), date(2020, 3, 31)]
SAMPLE = 100
NAMED = ("AAPL", "JPM", "BAC", "O", "SPG", "WFC", "AMT", "BAX", "COP", "MET", "PLD", "KO", "GE",
         "HAIN", "WDC")
STATIC_TABLES = ("index_membership", "ticker_aliases", "member_fates", "tickers", "companies",
                 "index_snapshots", "splits")


def _q(xs) -> str:
    return ",".join("'" + str(x).replace("'", "''") + "'" for x in xs)


def _qi(xs) -> str:
    return ",".join(str(int(x)) for x in xs)


def sample_symbols() -> tuple[list[str], list[int]]:
    """(price symbols, CIKs) of the sample: random members, named symbols, every ETF."""
    src = sqlite3.connect(SRC, uri=True)
    try:
        syms = sorted(set(symbol_map(src).values()))
        random.seed(3)
        pick = set(random.sample(syms, min(SAMPLE, len(syms)))) | {s for s in NAMED if s in syms}
        tick = pd.read_sql("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL", src)
        merges = merges_from_tickers(src)
        ciks = set(tick[tick["ticker"].isin(pick)]["cik"].astype(int))
        ciks |= {p for c in list(ciks) for p in merges.get(c, [])}
        ciks |= {int(c) for (c,) in src.execute(
            "SELECT cik FROM ticker_aliases WHERE cik IS NOT NULL") if c}
    finally:
        src.close()
    return sorted(pick | set(ALL_REFERENCE_ETFS) | {"SPY"}), sorted(ciks)


@pytest.fixture(scope="module")
def sample():
    if not DB.exists():
        pytest.skip(f"{DB} not found")
    return sample_symbols()


def _build(cut: date, prices: list[str], ciks: list[int]) -> sqlite3.Connection:
    m = sqlite3.connect("file::memory:", uri=True)
    m.execute("ATTACH DATABASE ? AS src", (SRC,))
    c = cut.isoformat()
    m.execute(f"CREATE TABLE prices AS SELECT * FROM src.prices WHERE symbol IN ({_q(prices)}) "
              "AND date <= ?", (c,))
    m.execute(f"CREATE TABLE facts AS SELECT * FROM src.facts WHERE cik IN ({_qi(ciks)}) "
              "AND filed <= ?", (c,))
    m.execute("CREATE TABLE insider_transactions AS SELECT * FROM src.insider_transactions "
              f"WHERE issuer_cik IN ({_qi(ciks)}) AND filing_date <= ?", (c,))
    m.execute("CREATE TABLE macro AS SELECT * FROM src.macro WHERE date <= ?", (c,))
    for t in STATIC_TABLES:
        m.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    m.execute("CREATE INDEX i_facts ON facts(cik, concept)")
    m.execute("CREATE INDEX i_prices ON prices(symbol, date)")
    m.commit()
    m.execute("DETACH DATABASE src")
    return m


def _differences(a: pd.DataFrame, b: pd.DataFrame) -> dict[str, int]:
    out = {}
    for col in a.columns:
        if col not in b.columns:
            out[col] = -1
            continue
        x, y = a[col], b[col]
        if x.dtype.kind in "fcbiu" and y.dtype.kind in "fcbiu":
            xv, yv = x.astype(float).to_numpy(), y.astype(float).to_numpy()
            diff = ~(np.isclose(xv, yv, rtol=1e-6, atol=1e-9) | (np.isnan(xv) & np.isnan(yv)))
        else:
            diff = x.astype(str).to_numpy() != y.astype(str).to_numpy()
        if diff.any():
            out[col] = int(diff.sum())
    return out


def test_evaluate_is_truncation_invariant_on_real_data(sample):
    prices, ciks = sample
    rules = load_rules()
    full_panels = load_panels(_build(END, prices, ciks), rules, end=END)
    full = evaluate(full_panels, rules)
    problems = []
    for cut in CUTS:
        part_panels = load_panels(_build(cut, prices, ciks), rules, end=cut)
        part = evaluate(part_panels, rules)
        ts = pd.Timestamp(cut)
        fund = {}  # every fundamental field of every symbol known at the cut, not only event rows
        for name, fb in part_panels.fundamentals.frames.items():
            fa = full_panels.fundamentals[name].loc[:ts, fb.columns]
            n = _differences(fa.astype(float), fb.reindex(index=fa.index).astype(float))
            if n:
                fund[name] = sum(n.values())
        a = full.rows[full.rows["date"] <= ts].set_index(["symbol", "date"]).sort_index()
        b = part.rows.set_index(["symbol", "date"]).sort_index()
        only_a, only_b = a.index.difference(b.index), b.index.difference(a.index)
        common = a.index.intersection(b.index)
        cols = _differences(a.loc[common].drop(columns=["pos"]), b.loc[common].drop(columns=["pos"]))
        cells = {}
        for name in ("eligible", "trap_fails", "member"):
            fa = getattr(full, name).loc[:ts]
            fb = getattr(part, name).reindex(index=fa.index, columns=fa.columns, fill_value=False)
            cells[name] = int((fa.to_numpy() != fb.to_numpy()).sum())
        da = full.dislocated_all[full.dislocated_all["date"] <= ts][["symbol", "date"]]
        db = part.dislocated_all[["symbol", "date"]]
        disl_diff = len(da.merge(db, how="outer", indicator=True).query("_merge != 'both'"))
        reg = full.regime.loc[:ts].astype(str).to_numpy() != part.regime.astype(str).to_numpy()
        print(f"cut {cut}: fundamental cells differing {fund or 0}; "
              f"rows {len(a)} vs {len(b)}, only-full {len(only_a)}, only-cut "
              f"{len(only_b)}, column diffs {cols}, cell diffs {cells}, dislocated_all diffs "
              f"{disl_diff}, regime diffs {int(reg.sum())}")
        if fund or len(only_a) or len(only_b) or cols or any(cells.values()) or disl_diff or reg.any():
            problems.append((cut, fund, len(only_a), len(only_b), cols, cells, disl_diff,
                             int(reg.sum())))
    assert not problems, problems
