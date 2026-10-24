"""A small synthetic database for signal and research tests.

Fifteen technology stocks (SIC 3674, one peer group, so at least eight peers), SPY and XLK,
2012-2016, with quarterly facts from Q4 2010. Normal companies grow revenue 2% a quarter at
stable margins and wiggle by about 1%. Each special stock exercises one gate:

- DIP: price drifts 25% lower over 40 trading days from 2015-09-01 while its business keeps
  growing. It should be the only setup. One CEO purchase (USD 375k) lands inside its drawdown.
- EXP: the same drop, but earnings, revenue, cash flow and equity are 40% of the peers', so its
  ratios sit above the peer median (gate 1 fails).
- SHK: a drop of about 19% that includes one -12% day (shock, gate 4 fails).
- TRAP: the same drop as DIP, but revenue peaks at Q4 2013 and then falls 8% a quarter (gate 3).
- OUT: dislocated like DIP and an index member, but no CIK or filings, so it is in
  `dislocated_all` and never in `rows` (a symbol outside every index has no panel column at all).

adj_close differs from close for every symbol (a slowly rising factor, like dividends).

Known limits: 10-K facts are quarterly durations like 10-Q facts (no annual or Q4-derived
rows), every fact is USD in one unit, and cash flows are reported per quarter, not
year-to-date. Real filings differ on all three."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analyzer.config import Rules, load_rules
from analyzer.store.db import connect, migrate

DIP = "DIP"
PEERS = [f"P{i:02d}" for i in range(11)]
DATES = pd.bdate_range("2012-01-02", "2016-12-30")
DROP_START = pd.Timestamp("2015-09-01")
DROP_DAYS = 40
FILING_LAG_DAYS = 40
EXP, SHK, TRAP, OUT = "EXP", "SHK", "TRAP", "OUT"
SPECIAL = [EXP, SHK, TRAP, OUT]
STOCKS = [DIP, *PEERS, *SPECIAL]
SCALE = {EXP: 0.4}                  # earnings, revenue, cash flow and equity relative to peers
INSIDER_FILED = "2015-09-21"


def fixture_rules() -> Rules:
    rules = load_rules()
    return dataclasses.replace(
        rules, research=dataclasses.replace(rules.research, panel_start="2012-01-01"))


def _closes(i: int, symbol: str) -> np.ndarray:
    t = np.arange(len(DATES))
    base = 100 * (1 + 0.0001 * t) * (1 + 0.01 * np.sin(t / 15 + i))
    k = DATES.searchsorted(DROP_START)
    factor = np.ones(len(DATES))
    if symbol == SHK:
        factor[k:k + 30] = np.linspace(1.0, 0.92, 30)
        factor[k + 30:] = 0.81                 # 0.92 -> 0.81: one -12% day
    elif symbol in (DIP, EXP, TRAP, OUT):
        factor[k:k + DROP_DAYS] = np.linspace(1.0, 0.75, DROP_DAYS)
        factor[k + DROP_DAYS:] = 0.75
    base = base * factor
    return base


def _price_rows(symbol: str, closes: np.ndarray) -> list[tuple]:
    adj = closes * (0.85 + 0.00012 * np.arange(len(DATES)))   # dividends: adj_close != close
    return [(symbol, d.date().isoformat(), c, c * 1.01, c * 0.99, c, a, 1_000_000.0, "fixture")
            for d, c, a in zip(DATES, closes, adj, strict=True)]


def _fact_rows(cik: int, symbol: str) -> list[tuple]:
    scale = SCALE.get(symbol, 1.0)
    rows = []
    ends = pd.date_range("2010-12-31", "2016-09-30", freq="QE")
    for k, end in enumerate(ends):
        start = (end - pd.offsets.QuarterEnd() + pd.Timedelta(days=1)).date().isoformat()
        filed = (end + pd.Timedelta(days=FILING_LAG_DAYS)).date().isoformat()
        e = end.date().isoformat()
        rev = 1000 * scale * (1.02 ** k if symbol != TRAP or k <= 12 else 1.02 ** 12 * 0.92 ** (k - 12))
        duration = {"revenue": ("us-gaap:Revenues", rev),
                    "operating_income": ("us-gaap:OperatingIncomeLoss", 0.20 * rev),
                    "net_income": ("us-gaap:NetIncomeLoss", 0.15 * rev),
                    "eps_diluted": ("us-gaap:EarningsPerShareDiluted", 0.15 * rev / 100),
                    "operating_cash_flow": ("us-gaap:NetCashProvidedByUsedInOperatingActivities", 0.18 * rev),
                    "capex": ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 0.05 * rev),
                    "depreciation_amortization": ("us-gaap:DepreciationDepletionAndAmortization", 0.03 * rev)}
        instant = {"stockholders_equity": ("us-gaap:StockholdersEquity", 4000.0 * scale),
                   "cash": ("us-gaap:CashAndCashEquivalentsAtCarryingValue", 1000.0),
                   "long_term_debt": ("us-gaap:LongTermDebtNoncurrent", 1000.0),
                   "shares_outstanding": ("dei:EntityCommonStockSharesOutstanding", 100.0)}
        form = "10-K" if end.month == 12 else "10-Q"
        for concept, (tag, value) in duration.items():
            rows.append((cik, concept, tag, "USD", start, e, value, filed, form, f"{cik}-{e}", "companyfacts"))
        for concept, (tag, value) in instant.items():
            rows.append((cik, concept, tag, "USD", "", e, value, filed, form, f"{cik}-{e}", "companyfacts"))
    return rows


def build_fixture_db(path):
    conn = connect(path)
    migrate(conn)
    t = np.arange(len(DATES))
    prices = _price_rows("SPY", 200 * (1 + 0.0001 * t)) + _price_rows("XLK", 50 * (1 + 0.0001 * t))
    facts = []
    for i, symbol in enumerate(STOCKS):
        cik = None if symbol == OUT else 1000 + i
        prices += _price_rows(symbol, _closes(i, symbol))
        conn.execute("INSERT INTO tickers (ticker, cik, name, gics_sector, sector_etf, updated_at) "
                     "VALUES (?, ?, ?, 'Information Technology', 'XLK', 'x')", (symbol, cik, symbol))
        if cik is not None:
            facts += _fact_rows(cik, symbol)
            conn.execute("INSERT INTO companies VALUES (?, ?, '3674', 'semis', 'x')", (cik, symbol))
        conn.execute("INSERT INTO index_membership VALUES ('sp500', ?, '2010-01-01', NULL)", (symbol,))
    conn.execute(
        "INSERT INTO insider_transactions (accession, seq, issuer_cik, filing_date, owner_names, "
        "relationship, officer_title, trans_date, trans_code, shares, price, acquired_disposed, "
        "source) VALUES ('ins-1', 0, 1000, ?, 'Jane Doe', 'Officer', 'Chief Executive Officer', "
        "?, 'P', 5000, 75.0, 'A', 'xml')", (INSIDER_FILED, INSIDER_FILED))
    with conn:
        conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)", prices)
        conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", facts)
    return conn
