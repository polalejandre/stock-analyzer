"""A small synthetic database for signal and research tests.

Twelve technology stocks (SIC 3674, one peer group), SPY and XLK, 2012-2016, with quarterly
facts from Q4 2010. Every company grows revenue 2% a quarter at stable margins. DIP's price
drifts 25% lower over 40 trading days from 2015-09-01 while its business keeps growing, so it
should become a setup; the peers only wiggle by about 1%."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analyzer.config import Rules, load_rules
from analyzer.store.db import connect, migrate

DIP = "DIP"
PEERS = [f"P{i:02d}" for i in range(11)]
STOCKS = [DIP, *PEERS]
DATES = pd.bdate_range("2012-01-02", "2016-12-30")
DROP_START = pd.Timestamp("2015-09-01")
DROP_DAYS = 40
FILING_LAG_DAYS = 40


def fixture_rules() -> Rules:
    rules = load_rules()
    return dataclasses.replace(
        rules, research=dataclasses.replace(rules.research, panel_start="2012-01-01"))


def _closes(i: int, symbol: str) -> np.ndarray:
    t = np.arange(len(DATES))
    base = 100 * (1 + 0.0001 * t) * (1 + 0.01 * np.sin(t / 15 + i))
    if symbol == DIP:
        k = DATES.searchsorted(DROP_START)
        factor = np.ones(len(DATES))
        factor[k:k + DROP_DAYS] = np.linspace(1.0, 0.75, DROP_DAYS)
        factor[k + DROP_DAYS:] = 0.75
        base = base * factor
    return base


def _price_rows(symbol: str, closes: np.ndarray) -> list[tuple]:
    return [(symbol, d.date().isoformat(), c, c * 1.01, c * 0.99, c, c, 1_000_000.0, "fixture")
            for d, c in zip(DATES, closes, strict=True)]


def _fact_rows(cik: int) -> list[tuple]:
    rows = []
    ends = pd.date_range("2010-12-31", "2016-09-30", freq="QE")
    for k, end in enumerate(ends):
        start = (end - pd.offsets.QuarterEnd() + pd.Timedelta(days=1)).date().isoformat()
        filed = (end + pd.Timedelta(days=FILING_LAG_DAYS)).date().isoformat()
        e = end.date().isoformat()
        rev = 1000 * 1.02 ** k
        duration = {"revenue": ("us-gaap:Revenues", rev),
                    "operating_income": ("us-gaap:OperatingIncomeLoss", 0.20 * rev),
                    "net_income": ("us-gaap:NetIncomeLoss", 0.15 * rev),
                    "eps_diluted": ("us-gaap:EarningsPerShareDiluted", 0.15 * rev / 100),
                    "operating_cash_flow": ("us-gaap:NetCashProvidedByUsedInOperatingActivities", 0.18 * rev),
                    "capex": ("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 0.05 * rev),
                    "depreciation_amortization": ("us-gaap:DepreciationDepletionAndAmortization", 0.03 * rev)}
        instant = {"stockholders_equity": ("us-gaap:StockholdersEquity", 4000.0),
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
        cik = 1000 + i
        prices += _price_rows(symbol, _closes(i, symbol))
        facts += _fact_rows(cik)
        conn.execute("INSERT INTO tickers (ticker, cik, name, gics_sector, sector_etf, updated_at) "
                     "VALUES (?, ?, ?, 'Information Technology', 'XLK', 'x')", (symbol, cik, symbol))
        conn.execute("INSERT INTO companies VALUES (?, ?, '3674', 'semis', 'x')", (cik, symbol))
        conn.execute("INSERT INTO index_membership VALUES ('sp500', ?, '2010-01-01', NULL)", (symbol,))
    with conn:
        conn.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?)", prices)
        conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", facts)
    return conn
